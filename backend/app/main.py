from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .core.config import assert_secure_secrets, get_settings
from .collectors.runner import run_crawl
from .db import SessionLocal, get_session, init_db
from .db.models import AdminUser, City, JobRun, Restaurant, User
from .services import (
    AdminAuthError,
    Extractor,
    FeedbackRateLimited,
    EmailAuthError,
    UgcError,
    UserAuthError,
    add_favorite,
    add_manual_alias,
    align_mentions,
    authenticate,
    authenticate_user,
    build_rank_snapshot,
    build_admin_stats,
    build_restaurant_detail,
    build_restaurant_sources,
    collect_shop_scores,
    confirm_review,
    crawl_overview,
    create_feedback,
    create_ugc_post,
    ensure_bootstrap_admin,
    extract_raw_content,
    get_rank,
    get_raw_content,
    hash_ip,
    ingest_raw_content,
    is_favorite,
    issue_token,
    issue_user_token,
    list_audit,
    list_cities,
    list_feedback,
    list_favorites,
    list_my_ugc,
    list_pending_reviews,
    list_restaurants_admin,
    list_restaurant_ugc,
    list_ugc_admin,
    login_or_register_via_email,
    make_rank_cache,
    merge_restaurants,
    normalize_email,
    pending_reviews_detail,
    rank_share_text,
    recommend_for_user,
    record_view,
    reject_review,
    register_user,
    remove_favorite,
    render_share_html,
    resolve_base_url,
    resolve_og_image,
    restaurant_share_text,
    review_ugc,
    run_all_cities,
    run_city_pipeline,
    save_upload,
    send_email_code,
    set_admin_password,
    set_restaurant_status,
    update_feedback_status,
    verify_email_code,
    verify_token,
    verify_user_token,
    write_audit,
)
from .services.metrics import (
    CONTENT_TYPE_LATEST,
    add_http_metrics,
    render_metrics,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 生产环境强校验弱默认密钥（文档 11 章）：命中即拒绝启动
    assert_secure_secrets(settings)
    init_db()
    # 按 ADMIN_PASSWORD 幂等初始化超管；留空则跳过（用 scripts/create_admin.py 建号）
    with SessionLocal() as session:
        ensure_bootstrap_admin(session, settings=settings)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# UGC 图片静态服务（V2.0）：目录不存在则创建；nginx 反代 /uploads/ 到这里
_uploads_dir = Path(settings.uploads_dir)
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP 指标中间件（文档 10.4：QPS / P95 / 错误率）
add_http_metrics(app)

extractor = Extractor(settings)
rank_cache = make_rank_cache(settings)


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, description="待抽取的文本片段")
    city_hint: str | None = Field(default=None, description="城市线索，如：成都")
    apply_confidence_filter: bool = Field(
        default=False, description="是否按 EXTRACT_CONFIDENCE_MIN 过滤低置信结果"
    )
    preprocess: bool = Field(
        default=False,
        description="true 时先执行清洗 + 切块流水线（文档 5.2）再抽取，适合原始长文",
    )


class IngestRequest(BaseModel):
    source: str = Field(description="数据源：dianping | xiaohongshu | forum | weibo | map")
    text: str = Field(min_length=1, description="原始文本（未清洗）")
    source_url: str | None = None
    city_hint: str | None = None
    raw_title: str | None = None


class FeedbackRequest(BaseModel):
    """纠错/举报（文档 9.3）：免登录，联系方式选填。"""

    restaurant_id: int | None = Field(default=None, description="关联店铺 id")
    type: Literal["info", "closed", "label", "other"] = Field(
        default="info", description="info 信息有误 | closed 已关停 | label 标签不当 | other 其他"
    )
    content: str = Field(min_length=1, description="纠错内容")
    contact: str | None = Field(default=None, max_length=128, description="可选联系方式")


class AuthRequest(BaseModel):
    """C 端注册/登录（文档 10.2 / V2.0）：用户名 + 口令换令牌。"""

    username: str = Field(min_length=2, max_length=32, description="用户名")
    password: str = Field(min_length=6, max_length=64, description="密码")


class FavoriteRequest(BaseModel):
    """收藏/取消收藏（文档 8.1 favorites / V2.0）。"""

    restaurant_id: int = Field(description="店铺 id")


class EmailSendRequest(BaseModel):
    """发送邮箱验证码（文档 10.2）。"""

    email: str = Field(min_length=5, max_length=254, description="邮箱")


class EmailLoginRequest(BaseModel):
    """邮箱验证码登录（文档 10.2）：无账号自动注册。"""

    email: str = Field(min_length=5, max_length=254, description="邮箱")
    code: str = Field(min_length=4, max_length=6, description="邮箱验证码")


class UgcCreateRequest(BaseModel):
    """发布打卡（文档 2.2 V2.0 UGC 含图片）。"""

    restaurant_id: int = Field(description="店铺 id")
    content: str = Field(min_length=1, description="打卡内容")
    images: list[str] = Field(default_factory=list, description="图片路径列表（先调上传接口）")


class UgcReviewRequest(BaseModel):
    """UGC 审核状态流转。"""

    status: Literal["approved", "rejected"]


def _client_ip(request: Request) -> str | None:
    """取真实客户端 IP：反代场景优先 X-Forwarded-For 首段。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


# --- 管理后台（文档 9.5）-----------------------------------------------------
# 独立登录 + 独立令牌，与 C 端隔离；所有写操作经 _audit 记审计日志。


def require_admin(
    request: Request, session: Session = Depends(get_session)
) -> AdminUser:
    """校验 Authorization: Bearer <token>；失败一律 401。"""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    try:
        payload = verify_token(token, settings=settings)
    except AdminAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.scalar(
        select(AdminUser).where(AdminUser.username == payload.get("sub"))
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user


def require_role(*roles: str):
    """RBAC 依赖工厂（文档 9.5 账号与权限）：限定角色访问，越权返回 403。

    角色矩阵：reviewer 只读；operator 可写运营操作；superadmin 全部
    （含账号管理）。roles 为空表示仅要求登录。
    """

    def dep(admin: AdminUser = Depends(require_admin)) -> AdminUser:
        if roles and admin.role not in roles:
            raise HTTPException(status_code=403, detail="当前角色无权限执行该操作")
        return admin

    return dep


# 可写运营操作：operator 及以上（reviewer 只读）
require_writer = require_role("operator", "superadmin")
# 账号管理：仅 superadmin
require_superadmin = require_role("superadmin")


def require_user(
    request: Request, session: Session = Depends(get_session)
) -> User:
    """C 端用户态鉴权（文档 10.2 / V2.0）：Bearer 令牌，失败一律 401。"""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    try:
        payload = verify_user_token(token, settings=settings)
    except UserAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.get(User, payload.get("uid"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user


def require_user_optional(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    """可选登录：带合法令牌返回用户，无/无效令牌返回 None（推荐、浏览事件用）。"""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    if not token:
        return None
    try:
        payload = verify_user_token(token, settings=settings)
    except UserAuthError:
        return None
    user = session.get(User, payload.get("uid"))
    return user if user and user.is_active else None


def _audit(
    request: Request,
    session: Session,
    admin: AdminUser,
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | int | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """记录管理后台写操作（文档 9.5 审计日志）。"""
    write_audit(
        session,
        operator=admin.username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before=before,
        after=after,
        ip_hash=hash_ip(_client_ip(request), settings=settings),
    )


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AdminUserUpsertRequest(BaseModel):
    """新增/重置管理后台账号（文档 9.5 RBAC，仅超管）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    role: Literal["superadmin", "operator", "reviewer"] = "operator"


class ConfirmReviewRequest(BaseModel):
    restaurant_id: int | None = Field(default=None, description="指定目标店铺，缺省用候选")


class MergeRequest(BaseModel):
    target_id: int = Field(description="合并目标店铺 id")


class AliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=128, description="待确认的店铺别名")


class RestaurantStatusRequest(BaseModel):
    status: Literal["active", "blocked"]


class FeedbackStatusRequest(BaseModel):
    status: Literal["pending", "processing", "resolved", "rejected"]


class CrawlRunRequest(BaseModel):
    city: str | None = Field(default=None, description="限定城市；缺省全部")
    sources: list[str] | None = Field(default=None, description="限定数据源；缺省全部 P0")
    mode: Literal["incremental", "full"] = "incremental"
    force: bool = Field(default=False, description="忽略夜间暂停")


@app.get("/health")
def health():
    return ok({"status": "up", "llm_mode": "mock" if settings.use_mock else "live"})


@app.get("/metrics")
def metrics(session: Session = Depends(get_session)):
    """Prometheus 指标（文档 10.4）。

    仅由 Prometheus 内网抓取，nginx 不反代该路径，故不对外暴露。
    """
    return Response(content=render_metrics(session), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/extract")
def extract(req: ExtractRequest):
    if req.preprocess:
        result = extractor.extract_document(
            req.text,
            city_hint=req.city_hint,
            apply_confidence_filter=req.apply_confidence_filter,
        )
    else:
        result = extractor.extract(
            text=req.text,
            city_hint=req.city_hint,
            apply_confidence_filter=req.apply_confidence_filter,
        )
    return ok(result.model_dump())


@app.post("/api/v1/ingest")
def ingest(req: IngestRequest, session: Session = Depends(get_session)):
    row = ingest_raw_content(
        session,
        source=req.source,
        raw_text=req.text,
        source_url=req.source_url,
        city_hint=req.city_hint,
        raw_title=req.raw_title,
        settings=settings,
    )
    session.commit()
    return ok(
        {
            "id": row.id,
            "status": row.status,
            "lang": row.lang,
            "low_trust": row.low_trust,
            "ad_hits": row.ad_hits or [],
            "chunk_count": row.chunk_count,
        }
    )


@app.post("/api/v1/raw-contents/{raw_content_id}/extract")
def extract_raw_content_endpoint(
    raw_content_id: int,
    apply_confidence_filter: bool = False,
    session: Session = Depends(get_session),
):
    try:
        result = extract_raw_content(
            session,
            raw_content_id,
            extractor=extractor,
            settings=settings,
            apply_confidence_filter=apply_confidence_filter,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="raw_content 不存在")
    session.commit()
    return ok(result.model_dump())


@app.get("/api/v1/scoring/shops")
def scoring_shops(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    """聚合 mention 计算店铺得分（文档 6；按 restaurant 实体聚合）。"""
    scores = collect_shop_scores(session, city_hint=city_hint, settings=settings)
    return ok([s.model_dump() for s in scores])


@app.post("/api/v1/alignment/run")
def alignment_run(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    """对未归并的 mention 执行实体对齐（文档 5.5）。"""
    result = align_mentions(session, city_hint=city_hint, settings=settings)
    session.commit()
    return ok(result.model_dump())


@app.get("/api/v1/restaurants")
def list_restaurants(
    city_hint: str | None = None, session: Session = Depends(get_session)
):
    stmt = select(Restaurant).order_by(Restaurant.id)
    if city_hint:
        stmt = stmt.join(City, Restaurant.city_id == City.id).where(City.name == city_hint)
    return ok(
        [
            {
                "id": r.id,
                "name": r.name,
                "name_norm": r.name_norm,
                "city": r.city.name if r.city else None,
                "address": r.address,
                "status": r.status,
                "mention_count": len(r.mentions),
            }
            for r in session.scalars(stmt).all()
        ]
    )


@app.get("/api/v1/cities")
def cities(session: Session = Depends(get_session)):
    """城市列表（文档 8.1）：City 表 ∪ raw_content.city_hint。"""
    return ok(list_cities(session))


@app.get("/api/v1/cities/search")
def cities_search(q: str = "", session: Session = Depends(get_session)):
    """城市搜索（文档 8.1）；q 为空则返回全部。"""
    rows = list_cities(session)
    if q:
        rows = [c for c in rows if q in c["name"]]
    return ok(rows)


@app.get("/api/v1/restaurants/{restaurant_id}")
def restaurant_detail(restaurant_id: int, session: Session = Depends(get_session)):
    """店铺详情（文档 8.1 / 9.2 detail）。"""
    detail = build_restaurant_detail(session, restaurant_id, settings=settings)
    if detail is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return ok(detail)


@app.get("/api/v1/restaurants/{restaurant_id}/sources")
def restaurant_sources(restaurant_id: int, session: Session = Depends(get_session)):
    """店铺来源引用列表（文档 8.1），做脱敏与外链提示（不内嵌第三方原文）。"""
    sources = build_restaurant_sources(session, restaurant_id)
    if sources is None:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return ok(sources)


@app.post("/api/v1/feedback")
def submit_feedback(
    req: FeedbackRequest, request: Request, session: Session = Depends(get_session)
):
    """纠错/举报（文档 9.3 详情页纠错入口 / 14 章合规验收）。

    免登录提交（账号体系属 V2），改以 IP 哈希限流；只落 ip_hash 不存原始 IP。
    """
    try:
        row = create_feedback(
            session,
            content=req.content,
            settings=settings,
            restaurant_id=req.restaurant_id,
            type=req.type,
            contact=req.contact,
            ip_hash=hash_ip(_client_ip(request), settings=settings),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FeedbackRateLimited:
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
    session.commit()
    return ok({"id": row.id, "status": row.status})


# --- C 端账号与收藏（文档 10.2 账号体系 / 8.1 favorites / V2.0）--------------


def _auth_payload(user: User, settings_obj) -> dict:
    token, expires_at = issue_user_token(user, settings=settings_obj)
    return {
        "token": token,
        "expires_at": expires_at.isoformat(),
        "username": user.username,
    }


@app.post("/api/v1/auth/register")
def auth_register(req: AuthRequest, session: Session = Depends(get_session)):
    """C 端注册（文档 10.2）：成功即视为登录，直接签发令牌。"""
    try:
        user = register_user(session, req.username, req.password, settings=settings)
    except ValueError as exc:
        code = 409 if "占用" in str(exc) else 422
        raise HTTPException(status_code=code, detail=str(exc))
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return ok(_auth_payload(user, settings))


@app.post("/api/v1/auth/login")
def auth_login(req: AuthRequest, session: Session = Depends(get_session)):
    """C 端登录（文档 10.2）：口令换取用户态令牌，与后台令牌隔离。"""
    try:
        user = authenticate_user(session, req.username, req.password)
    except UserAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return ok(_auth_payload(user, settings))


@app.get("/api/v1/auth/me")
def auth_me(user: User = Depends(require_user)):
    return ok({"username": user.username})


@app.get("/api/v1/favorites")
def favorites_list(
    user: User = Depends(require_user), session: Session = Depends(get_session)
):
    """我的收藏列表（文档 8.1 mine / V2.0）。"""
    return ok(list_favorites(session, user))


@app.post("/api/v1/favorites")
def favorites_add(
    req: FavoriteRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """收藏店铺；重复收藏幂等返回 created=False。"""
    try:
        _, created = add_favorite(session, user.id, req.restaurant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ok({"restaurant_id": req.restaurant_id, "favorited": True, "created": created})


@app.delete("/api/v1/favorites/{restaurant_id}")
def favorites_remove(
    restaurant_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """取消收藏；未收藏时幂等返回 favorited=False。"""
    remove_favorite(session, user.id, restaurant_id)
    return ok({"restaurant_id": restaurant_id, "favorited": False})


@app.get("/api/v1/favorites/{restaurant_id}")
def favorites_status(
    restaurant_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """详情页收藏态查询。"""
    return ok(
        {
            "restaurant_id": restaurant_id,
            "favorited": is_favorite(session, user.id, restaurant_id),
        }
    )


# --- C 端短信登录 / UGC / 推荐（文档 10.2 / 2.2 V2.0）-----------------------


@app.post("/api/v1/auth/email/send")
def auth_email_send(
    req: EmailSendRequest, request: Request, session: Session = Depends(get_session)
):
    """发送邮箱验证码：限流按邮箱/IP；未配 SMTP 发信账号走 mock（日志打印）。"""
    try:
        result = send_email_code(
            session,
            req.email,
            settings=settings,
            ip_hash=hash_ip(_client_ip(request), settings=settings),
        )
    except EmailAuthError as exc:
        raise HTTPException(status_code=429 if "频繁" in str(exc) else 422, detail=str(exc))
    payload = {"mock": result["mock"], "ttl_minutes": settings.email_code_ttl_minutes}
    if result["dev_code"]:
        # 仅 mock（本地联调）返回验证码；生产配置发信账号后恒为 None
        payload["dev_code"] = result["dev_code"]
    return ok(payload)


@app.post("/api/v1/auth/email/login")
def auth_email_login(req: EmailLoginRequest, session: Session = Depends(get_session)):
    """验证码登录：邮箱无账号时自动注册，成功即签发令牌。"""
    try:
        verify_email_code(session, req.email, req.code, settings=settings)
        user, created = login_or_register_via_email(
            session, normalize_email(req.email), settings=settings
        )
    except EmailAuthError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except UserAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    user.last_login_at = datetime.now(timezone.utc)
    session.commit()
    return ok({**_auth_payload(user, settings), "created": created})


@app.post("/api/v1/ugc")
def ugc_create(
    req: UgcCreateRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """发布打卡（V2.0）：先审后显，默认 pending。"""
    try:
        post = create_ugc_post(
            session,
            user.id,
            req.restaurant_id,
            req.content,
            settings=settings,
            images=req.images,
        )
    except UgcError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ok({"id": post.id, "status": post.status})


@app.get("/api/v1/ugc")
def ugc_list(
    restaurant_id: int, limit: int = 20, session: Session = Depends(get_session)
):
    """店铺打卡列表（仅审核通过，浏览类接口免登录）。"""
    return ok(list_restaurant_ugc(session, restaurant_id, limit=limit))


@app.get("/api/v1/ugc/mine")
def ugc_mine(
    user: User = Depends(require_user), session: Session = Depends(get_session)
):
    """我的打卡（含待审/被拒）。"""
    return ok(list_my_ugc(session, user.id))


@app.post("/api/v1/uploads")
async def upload_image(
    request: Request, _: User = Depends(require_user)
):
    """上传打卡图片（V2.0）：单图 ≤5MB，JPG/PNG/WebP，登录用户。"""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    data = await request.body()
    try:
        path = save_upload(data, content_type, settings=settings)
    except UgcError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ok({"path": path})


@app.post("/api/v1/views")
def record_view_event(
    req: FavoriteRequest,
    user: User | None = Depends(require_user_optional),
    session: Session = Depends(get_session),
):
    """记录详情页浏览事件（V2.0 推荐依据）；未登录也接受（user 为 None）。"""
    record_view(session, req.restaurant_id, user.id if user else None)
    return ok({"recorded": True})


@app.get("/api/v1/recommend")
def recommend(
    limit: int = 6,
    user: User | None = Depends(require_user_optional),
    session: Session = Depends(get_session),
):
    """个性化推荐（V2.0）：登录用户按口味召回，未登录回退全城热门。"""
    return ok(recommend_for_user(session, user.id if user else None, limit=limit))


@app.get("/api/v1/admin/ugc")
def admin_ugc_list(
    status: str | None = None,
    limit: int = 50,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """UGC 审核列表（文档 9.5 反馈处理同款流程）。"""
    return ok(list_ugc_admin(session, status=status, limit=limit))


@app.patch("/api/v1/admin/ugc/{post_id}")
def admin_ugc_review(
    post_id: int,
    req: UgcReviewRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """UGC 审核流转（approved/rejected），写审计日志。"""
    try:
        post, before = review_ugc(session, post_id, req.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "ugc.review",
        target_type="ugc_post",
        target_id=post.id,
        before={"status": before},
        after={"status": post.status},
    )
    session.commit()
    return ok({"id": post.id, "status": post.status})


@app.post("/api/v1/admin/login")
def admin_login(
    req: AdminLoginRequest, request: Request, session: Session = Depends(get_session)
):
    """管理后台登录（文档 9.5）：口令换独立令牌，与 C 端鉴权隔离。"""
    try:
        user = authenticate(session, req.username, req.password)
    except AdminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    token, expires_at = issue_token(user, settings=settings)
    user.last_login_at = datetime.now(timezone.utc)
    _audit(
        request,
        session,
        user,
        "admin.login",
        target_type="admin_user",
        target_id=user.id,
    )
    session.commit()
    return ok(
        {
            "token": token,
            "expires_at": expires_at.isoformat(),
            "username": user.username,
            "role": user.role,
        }
    )


@app.get("/api/v1/admin/me")
def admin_me(admin: AdminUser = Depends(require_admin)):
    return ok({"username": admin.username, "role": admin.role})


@app.get("/api/v1/admin/reviews")
def admin_reviews(
    limit: int = 100,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """实体对齐灰区队列（文档 9.5 店铺审核）。"""
    return ok(pending_reviews_detail(session, limit=limit))


@app.post("/api/v1/admin/reviews/{review_id}/confirm")
def admin_review_confirm(
    review_id: int,
    req: ConfirmReviewRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """确认灰区 mention 归并（可指定目标店铺）。"""
    try:
        review = confirm_review(session, review_id, req.restaurant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "review.confirm",
        target_type="alignment_review",
        target_id=review_id,
        before={"status": "pending"},
        after={"status": review.status, "restaurant_id": req.restaurant_id},
    )
    session.commit()
    return ok({"id": review.id, "status": review.status})


@app.post("/api/v1/admin/reviews/{review_id}/reject")
def admin_review_reject(
    review_id: int,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """驳回灰区归并（保持 mention 未归并）。"""
    try:
        review = reject_review(session, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "review.reject",
        target_type="alignment_review",
        target_id=review_id,
        before={"status": "pending"},
        after={"status": review.status},
    )
    session.commit()
    return ok({"id": review.id, "status": review.status})


@app.get("/api/v1/admin/restaurants")
def admin_restaurants(
    city: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """店铺审核列表（文档 9.5）：城市/状态/关键词筛选 + 分页。"""
    items, total = list_restaurants_admin(
        session, city=city, status=status, q=q, limit=limit, offset=offset
    )
    return ok({"items": items, "total": total})


@app.post("/api/v1/admin/restaurants/{restaurant_id}/merge")
def admin_restaurant_merge(
    restaurant_id: int,
    req: MergeRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """店铺合并（文档 9.5）：source 并入 target，危险操作需前端二次确认。"""
    try:
        source, target, moved = merge_restaurants(session, restaurant_id, req.target_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "restaurant.merge",
        target_type="restaurant",
        target_id=source.id,
        before={"status": "active"},
        after={
            "status": source.status,
            "merged_into": target.id,
            "mentions_moved": moved,
        },
    )
    session.commit()
    return ok(
        {
            "source_id": source.id,
            "target_id": target.id,
            "status": source.status,
            "mentions_moved": moved,
        }
    )


@app.post("/api/v1/admin/restaurants/{restaurant_id}/aliases")
def admin_restaurant_alias(
    restaurant_id: int,
    req: AliasRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """人工确认店铺别名（文档 9.5）。"""
    try:
        alias = add_manual_alias(session, restaurant_id, req.alias)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if alias is None:
        return ok({"created": False, "restaurant_id": restaurant_id})
    _audit(
        request,
        session,
        admin,
        "restaurant.alias_add",
        target_type="restaurant",
        target_id=restaurant_id,
        after={"alias": alias.alias, "alias_norm": alias.alias_norm},
    )
    session.commit()
    return ok({"created": True, "id": alias.id, "alias": alias.alias})


@app.post("/api/v1/admin/restaurants/{restaurant_id}/status")
def admin_restaurant_status(
    restaurant_id: int,
    req: RestaurantStatusRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """屏蔽 / 恢复店铺（文档 9.5）；屏蔽后下次榜单重排即被剔除。"""
    try:
        restaurant, before = set_restaurant_status(session, restaurant_id, req.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "restaurant.set_status",
        target_type="restaurant",
        target_id=restaurant.id,
        before={"status": before},
        after={"status": restaurant.status},
    )
    session.commit()
    return ok({"id": restaurant.id, "status": restaurant.status})


@app.get("/api/v1/admin/crawl")
def admin_crawl(
    limit: int = 20,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """采集监控（文档 9.5）：最近任务 + 熔断状态 + 各源最近统计。"""
    return ok(crawl_overview(session, limit=limit))


@app.post("/api/v1/admin/crawl/run")
def admin_crawl_run(
    req: CrawlRunRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """手动触发采集跑批（文档 9.5）；同步执行并落 job_run。"""
    job = run_crawl(
        session,
        settings,
        sources=req.sources,
        mode=req.mode,
        city_hint=req.city,
        force=req.force,
    )
    _audit(
        request,
        session,
        admin,
        "crawl.run",
        target_type="job_run",
        target_id=job.id,
        after={
            "city": req.city,
            "sources": req.sources,
            "mode": req.mode,
            "status": job.status,
        },
    )
    session.commit()
    return ok(
        {
            "job_id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "stats": job.stats or {},
            "error": job.error,
        }
    )


def _feedback_row(row) -> dict:
    return {
        "id": row.id,
        "restaurant_id": row.restaurant_id,
        "type": row.type,
        "content": row.content,
        "contact": row.contact,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@app.get("/api/v1/admin/feedback")
def admin_feedback(
    limit: int = 50,
    status: str | None = None,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """纠错工单列表（文档 9.5 反馈处理）。"""
    return ok([_feedback_row(r) for r in list_feedback(session, limit=limit, status=status)])


@app.patch("/api/v1/admin/feedback/{feedback_id}")
def admin_feedback_update(
    feedback_id: int,
    req: FeedbackStatusRequest,
    request: Request,
    admin: AdminUser = Depends(require_writer),
    session: Session = Depends(get_session),
):
    """工单状态流转（文档 9.5）：pending → processing → resolved/rejected。"""
    try:
        row, before = update_feedback_status(session, feedback_id, req.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    _audit(
        request,
        session,
        admin,
        "feedback.update_status",
        target_type="feedback",
        target_id=row.id,
        before={"status": before},
        after={"status": row.status},
    )
    session.commit()
    return ok(_feedback_row(row))


@app.get("/api/v1/admin/audit")
def admin_audit(
    limit: int = 50,
    action: str | None = None,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """审计日志（文档 9.5）：所有写操作的操作人/时间/前后值。"""
    return ok(
        [
            {
                "id": r.id,
                "operator": r.operator,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "before": r.before,
                "after": r.after,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in list_audit(session, limit=limit, action=action)
        ]
    )


@app.get("/api/v1/admin/users")
def admin_users(
    _: AdminUser = Depends(require_superadmin),
    session: Session = Depends(get_session),
):
    """账号列表（文档 9.5 RBAC）：仅超管可见，口令哈希不外泄。"""
    users = session.scalars(select(AdminUser).order_by(AdminUser.id)).all()
    return ok(
        [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "is_active": u.is_active,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ]
    )


@app.post("/api/v1/admin/users")
def admin_user_upsert(
    req: AdminUserUpsertRequest,
    request: Request,
    admin: AdminUser = Depends(require_superadmin),
    session: Session = Depends(get_session),
):
    """新增/重置账号并指定角色（文档 9.5 RBAC）：仅超管，写审计日志。"""
    user = set_admin_password(
        session, req.username, req.password, settings=settings, role=req.role
    )
    _audit(
        request,
        session,
        admin,
        "admin.user_upsert",
        target_type="admin_user",
        target_id=user.id,
        after={"username": user.username, "role": user.role},
    )
    session.commit()
    return ok({"id": user.id, "username": user.username, "role": user.role})


@app.get("/api/v1/admin/stats")
def admin_stats(
    days: int = 14,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """数据看板聚合（文档 9.5 数据看板）：任意管理角色可读。"""
    days = max(1, min(90, days))
    return ok(build_admin_stats(session, days=days))


# --- 分享预览（文档 9.4 / 14 章验收）----------------------------------------
# nginx 按爬虫 UA 把 /detail/:id、/rank 分流到以下端点；返回动态 og 元信息 HTML，
# 脚本再把误判的真人浏览器送回对应 SPA 路由。


@app.get("/share/restaurant/{restaurant_id}", response_class=HTMLResponse)
def share_restaurant(
    restaurant_id: int, request: Request, session: Session = Depends(get_session)
):
    base = resolve_base_url(request, settings)
    detail = build_restaurant_detail(session, restaurant_id, settings=settings)
    if detail is None:
        return HTMLResponse(
            render_share_html(
                title=f"没有找到这家店 · {settings.share_site_name}",
                description="它可能已被合并或下架",
                base_url=base,
                page_path="/",
                site_name=settings.share_site_name,
                image_url=resolve_og_image(base, settings),
            ),
            status_code=404,
        )

    title, description = restaurant_share_text(detail)
    return HTMLResponse(
        render_share_html(
            title=title,
            description=description,
            base_url=base,
            page_path=f"/detail/{restaurant_id}",
            site_name=settings.share_site_name,
            image_url=resolve_og_image(base, settings),
        )
    )


@app.get("/share/rank", response_class=HTMLResponse)
def share_rank(
    city: str, request: Request, session: Session = Depends(get_session)
):
    base = resolve_base_url(request, settings)
    data = get_rank(session, city, page=1, page_size=3, cache=rank_cache)
    items = (data or {}).get("items") or []
    title, description = rank_share_text(city, items)
    return HTMLResponse(
        render_share_html(
            title=title,
            description=description,
            base_url=base,
            page_path=f"/rank?city={quote(city)}",
            site_name=settings.share_site_name,
            image_url=resolve_og_image(base, settings),
        )
    )


@app.get("/api/v1/alignment/reviews")
def alignment_reviews(session: Session = Depends(get_session)):
    return ok(
        [
            {
                "id": rv.id,
                "mention_id": rv.mention_id,
                "candidate_restaurant_id": rv.candidate_restaurant_id,
                "score": rv.score,
                "reason": rv.reason,
            }
            for rv in list_pending_reviews(session)
        ]
    )


@app.get("/api/v1/rank")
def rank(
    city: str,
    page: int = 1,
    page_size: int = 20,
    cuisine: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    area: str | None = None,
    days: int | None = None,
    session: Session = Depends(get_session),
):
    """读取该城市最新榜单快照（文档 8.2）。无快照 → 2001 语义。

    days 为时间维度（文档 2.2 V1.1）：传 90 表示只看近 90 天内被提及的店。
    """
    if days is not None and days <= 0:
        raise HTTPException(status_code=400, detail="days 必须为正整数")
    data = get_rank(
        session,
        city,
        page=max(1, page),
        page_size=max(1, min(100, page_size)),
        cuisine=cuisine,
        price_min=price_min,
        price_max=price_max,
        area=area,
        days=min(days, 3650) if days else None,
        cache=rank_cache,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="该城市暂无榜单（正在收录）")
    return ok(data)


@app.post("/api/v1/rank/build")
def rank_build(city: str, session: Session = Depends(get_session)):
    """生成该城市榜单快照（文档 6.4 流水线第 4–5 步，供运营/定时任务触发）。"""
    try:
        snapshot = build_rank_snapshot(session, city, settings=settings, cache=rank_cache)
    except LookupError:
        raise HTTPException(status_code=404, detail="城市不存在")
    session.commit()
    return ok(
        {
            "snapshot_id": snapshot.id,
            "city": city,
            "generated_at": snapshot.generated_at.isoformat()
            if snapshot.generated_at
            else None,
            "algorithm_ver": snapshot.algorithm_ver,
            "total": len(snapshot.items or []),
        }
    )


@app.post("/api/v1/jobs/run")
def jobs_run(city: str | None = None, session: Session = Depends(get_session)):
    """触发城市流水线：抽取→对齐→打分→榜单快照（文档 4.4 / 6.4）。

    不传 city 则跑全部有内容的城市（供定时任务/运维调用）。
    失败会写入 job_run（GET /api/v1/admin/jobs 可查），并返回 500。
    """
    if city:
        jobs = [run_city_pipeline(session, city, settings=settings, cache=rank_cache)]
    else:
        jobs = run_all_cities(session, settings=settings, cache=rank_cache)
    return ok(
        [
            {
                "job_id": j.id,
                "job_type": j.job_type,
                "status": j.status,
                "stats": j.stats or {},
                "error": j.error,
            }
            for j in jobs
        ]
    )


@app.get("/api/v1/admin/jobs")
def admin_jobs(
    limit: int = 20,
    _: AdminUser = Depends(require_admin),
    session: Session = Depends(get_session),
):
    """最近的任务运行记录（文档 8.2 / 7.2 job_run）。"""
    rows = session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(min(100, max(1, limit)))).all()
    return ok(
        [
            {
                "id": r.id,
                "job_type": r.job_type,
                "city": r.city.name if r.city else None,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stats": r.stats or {},
                "error": r.error,
            }
            for r in rows
        ]
    )


@app.get("/api/v1/raw-contents/{raw_content_id}")
def read_raw_content(raw_content_id: int, session: Session = Depends(get_session)):
    row = get_raw_content(session, raw_content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="raw_content 不存在")
    return ok(
        {
            "id": row.id,
            "source": row.source,
            "city_hint": row.city_hint,
            "status": row.status,
            "lang": row.lang,
            "low_trust": row.low_trust,
            "ad_hits": row.ad_hits or [],
            "chunk_count": row.chunk_count,
            "cleaned_text": row.cleaned_text,
            "chunks": [
                {
                    "chunk_index": c.chunk_index,
                    "text": c.text,
                    "start_offset": c.start_offset,
                    "end_offset": c.end_offset,
                    "token_estimate": c.token_estimate,
                    "status": c.status,
                }
                for c in row.chunks
            ],
            "mentions": [
                {
                    "id": m.id,
                    "shop_name_raw": m.shop_name_raw,
                    "address_text": m.address_text,
                    "dishes": m.dishes or [],
                    "sentiment": m.sentiment,
                    "praise_keywords": m.praise_keywords or [],
                    "complaints": m.complaints or [],
                    "is_recommendation": m.is_recommendation,
                    "confidence": m.confidence,
                    "evidence_span": m.evidence_span,
                    "evidence_start": m.evidence_start,
                    "evidence_end": m.evidence_end,
                }
                for m in row.mentions
            ],
        }
    )