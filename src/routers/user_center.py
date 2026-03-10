import json
import re
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.afdian_service import afdian_service
from core.config import settings
from core.dependencies import (
    get_db,
    get_effective_user_limit_rule,
    hash_password,
    is_user_expired,
    verify_password,
)
from core.logging_config import logger
from db.models import PaymentOrder, User

router = APIRouter()


DAY_LIMIT_RULE_PATTERN = re.compile(r"^\s*(\d+)\s*/\s*day\s*$", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
ACTION_ALIASES = {
    "renew_1m": "renew",
    "boost_1m": "upgrade",
    "renew": "renew",
    "upgrade": "upgrade",
    "downgrade": "downgrade",
}
PRICE_EPSILON = 0.0001


def _is_test_user(username: str) -> bool:
    configured = (settings.LIMIT_PUBLIC_TEST_USER or "").strip()
    if configured and username == configured:
        return True
    return username.lower() == "testuser"


class PaymentLinkRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    action: str = Field("renew")
    months: int = Field(1, ge=1, le=24)
    target_monthly_price: Optional[float] = None
    guide_token: Optional[str] = None
    plan_price: Optional[float] = None
    custom_price: Optional[float] = None


class PaymentGuideRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)


def _get_price_step() -> float:
    # Spec: fixed 5 CNY step.
    return 5.0


def _get_resource_increment_per_step() -> int:
    return max(1, int(settings.AFDIAN_BOOST_DAILY_INCREMENT or 1500))


def _build_payment_guide_token(payload: Dict[str, Any]) -> str:
    return afdian_service.build_custom_id(
        username="user-center",
        password="guide",
        action="payment_guide",
        extra_payload=payload,
    )


def _parse_payment_guide_token(token: str) -> Dict[str, Any]:
    payload = afdian_service.parse_custom_id(token)
    if str(payload.get("action") or "").strip().lower() != "payment_guide":
        raise ValueError("guide_token invalid")
    return payload


class ActivateRequest(BaseModel):
    remark: Optional[str] = None
    custom_id: Optional[str] = None
    out_trade_no: Optional[str] = None


@router.post("/payment-guide")
async def payment_guide(payload: PaymentGuideRequest, db: Session = Depends(get_db)):
    username = payload.username.strip()
    if _is_test_user(username):
        return {
            "username": username,
            "is_test_user": True,
            "status": "test_user",
        }

    user = db.query(User).filter(User.username == username).first()
    if user and not verify_password(payload.password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    # For non-existing users, still allow guide generation based on default rule.
    return _build_payment_guide_response(user, username)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _extend_date(current: Optional[datetime], days: int) -> datetime:
    now_utc = _utc_now_naive()
    base = _normalize_naive_utc(current) or now_utc
    if base < now_utc:
        base = now_utc
    return base + timedelta(days=max(1, days))


def _safe_parse_amount(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return amount if amount >= 0 else None
    if not isinstance(value, str):
        return None

    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return None

    try:
        amount = float(cleaned)
        return amount if amount >= 0 else None
    except ValueError:
        match = NUMBER_PATTERN.search(cleaned)
        if not match:
            return None
        amount = float(match.group(0))
        return amount if amount >= 0 else None


def _extract_order_amount(order: dict) -> Optional[float]:
    for key in ("show_amount", "total_amount", "amount", "pay_amount"):
        amount = _safe_parse_amount(order.get(key))
        if amount is not None:
            return amount
    return None


def _extract_daily_limit(limit_rule: Optional[str]) -> Optional[int]:
    if not limit_rule:
        return None
    for part in (segment.strip() for segment in limit_rule.split(";") if segment.strip()):
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched:
            return int(matched.group(1))
    return None


def _normalize_action(raw_action: Optional[str]) -> str:
    action = (raw_action or "").strip().lower()
    normalized = ACTION_ALIASES.get(action)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported action")
    return normalized


def _get_user_price_and_limit_rule(user: Optional[User]) -> tuple[float, str]:
    if user is None:
        rule = (settings.AFDIAN_DEFAULT_LIMIT_RULE or "").strip()
        return _infer_plan_price_from_rule(rule), rule

    effective_rule = get_effective_user_limit_rule(user)
    price = _infer_plan_price_from_rule(effective_rule)
    return price, effective_rule


def _is_downgrade_allowed(user: Optional[User], is_expired: bool, current_price: float) -> bool:
    # Spec: allow downgrade when account expired.
    if user is None:
        return False
    if not is_expired:
        return False
    return current_price > _get_price_step() + PRICE_EPSILON


def _build_payment_guide_response(user: Optional[User], username: str) -> Dict[str, Any]:
    price_step = int(round(_get_price_step()))
    resource_step = _get_resource_increment_per_step()
    current_price, effective_rule = _get_user_price_and_limit_rule(user)

    is_expired = False
    if user is not None:
        is_expired, _ = is_user_expired(user)

    allowed_actions = ["renew", "upgrade"]
    if _is_downgrade_allowed(user, is_expired, current_price):
        allowed_actions.append("downgrade")

    guide_payload = {
        "v": 1,
        "username": username,
        "ts": int(datetime.now(timezone.utc).timestamp()),
        "current_price": round(float(current_price), 2),
        "price_step": price_step,
        "resource_step": resource_step,
        "effective_rule": effective_rule,
        "is_expired": is_expired,
    }
    guide_token = _build_payment_guide_token(guide_payload)

    renew_option = {
        "allowed": True,
        "readonly": True,
        "default_monthly_price": round(float(current_price), 2),
        "min_monthly_price": round(float(current_price), 2),
        "max_monthly_price": round(float(current_price), 2),
        "step": price_step,
        "target_resource_quota_preview": _calculate_daily_limit_from_price(current_price),
    }

    upgrade_option = {
        "allowed": True,
        "readonly": False,
        "default_monthly_price": round(float(max(current_price + price_step, price_step)), 2),
        "min_monthly_price": round(float(max(current_price + price_step, price_step)), 2),
        "max_monthly_price": None,
        "step": price_step,
    }

    downgrade_option = {
        "allowed": _is_downgrade_allowed(user, is_expired, current_price),
        "readonly": False,
        "default_monthly_price": round(float(max(price_step, current_price - price_step)), 2),
        "min_monthly_price": float(price_step),
        "max_monthly_price": round(float(max(price_step, current_price - price_step)), 2),
        "step": price_step,
    }

    display_monthly_price = round(float(current_price), 2)
    display_plan_name = f"￥{display_monthly_price:.2f}/月"
    display_resource_summary = f"{_calculate_daily_limit_from_price(current_price)}/day"

    anchor_reason = "expired_downgrade_allowed" if downgrade_option["allowed"] else "current"

    return {
        "username": username,
        "is_test_user": _is_test_user(username),
        "is_expired": is_expired,
        "status": "ok",
        "price_step": price_step,
        "resource_increment_per_step": resource_step,
        "display_plan_name": display_plan_name,
        "display_monthly_price": display_monthly_price,
        "display_resource_quota": _calculate_daily_limit_from_price(current_price),
        "display_resource_summary": display_resource_summary,
        "anchor_reason": anchor_reason,
        "allowed_actions": allowed_actions,
        "renew_option": renew_option,
        "upgrade_option": upgrade_option,
        "downgrade_option": downgrade_option,
        "guide_token": guide_token,
    }


def _normalize_months(raw_months: object) -> int:
    try:
        months = int(raw_months)
    except (TypeError, ValueError):
        months = 1
    return max(1, min(24, months))


def _normalize_plan_price(raw_price: Optional[float]) -> float:
    price = _safe_parse_amount(raw_price)
    if price is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="plan_price is invalid")

    step = _get_price_step()
    if price + PRICE_EPSILON < step:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"套餐月价最低为 {step:.2f}",
        )

    units = float(Decimal(str(price / step)).to_integral_value(rounding=ROUND_FLOOR))
    normalized = round(units * step, 2)
    if normalized + PRICE_EPSILON < step:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"套餐月价最低为 {step:.2f}",
        )
    return normalized


def _calculate_daily_limit_from_price(plan_price: float) -> int:
    step = _get_price_step()
    increment = _get_resource_increment_per_step()
    units = int((Decimal(str(plan_price)) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR))
    return max(1, units) * increment


def _calculate_price_from_daily_limit(daily_limit: Optional[int]) -> Optional[float]:
    if daily_limit is None or daily_limit <= 0:
        return None
    increment = _get_resource_increment_per_step()
    step = _get_price_step()
    units = max(1, int((Decimal(str(daily_limit)) / Decimal(str(increment))).to_integral_value(rounding=ROUND_FLOOR)))
    return round(float(Decimal(units) * Decimal(str(step))), 2)


def _infer_plan_price_from_rule(limit_rule: Optional[str]) -> float:
    daily_limit = _extract_daily_limit(limit_rule)
    inferred = _calculate_price_from_daily_limit(daily_limit)
    if inferred is not None:
        return inferred
    return round(_get_price_step(), 2)


def _replace_daily_limit_rule(base_rule: Optional[str], daily_limit: int) -> str:
    desired_daily = max(1, int(daily_limit))
    parts = [segment.strip() for segment in (base_rule or "").split(";") if segment.strip()]
    if not parts:
        return f"{desired_daily}/day"

    updated_parts = []
    replaced = False
    for part in parts:
        matched = DAY_LIMIT_RULE_PATTERN.match(part)
        if matched and not replaced:
            updated_parts.append(f"{desired_daily}/day")
            replaced = True
        else:
            updated_parts.append(part)

    if not replaced:
        updated_parts.insert(0, f"{desired_daily}/day")
    return ";".join(updated_parts)


def _build_limit_rule_for_plan(base_rule: Optional[str], plan_price: float) -> str:
    return _replace_daily_limit_rule(base_rule, _calculate_daily_limit_from_price(plan_price))


def _find_local_order(
    db: Session,
    out_trade_no: Optional[str],
    order_no: Optional[str],
    custom_id: Optional[str],
) -> Optional[PaymentOrder]:
    if out_trade_no:
        order = db.query(PaymentOrder).filter(PaymentOrder.out_trade_no == out_trade_no).first()
        if order:
            return order
    if order_no:
        order = db.query(PaymentOrder).filter(PaymentOrder.order_no == order_no).first()
        if order:
            return order
    if custom_id:
        return db.query(PaymentOrder).filter(PaymentOrder.custom_id == custom_id).first()
    return None


def _ensure_order_record(
    db: Session,
    order_no: Optional[str],
    out_trade_no: Optional[str],
    custom_id: Optional[str],
) -> PaymentOrder:
    record = _find_local_order(db, out_trade_no, order_no, custom_id)
    if record is None:
        record = PaymentOrder(
            order_no=order_no or None,
            out_trade_no=out_trade_no or None,
            custom_id=custom_id or None,
            remark=custom_id or None,
            status="pending",
            created_at=_utc_now_naive(),
            updated_at=_utc_now_naive(),
        )
        db.add(record)
    return record


@router.post("/payment-link")
async def create_payment_link(payload: PaymentLinkRequest, db: Session = Depends(get_db)):
    action = _normalize_action(payload.action)
    months = _normalize_months(payload.months)

    username = payload.username.strip()
    password = payload.password
    if _is_test_user(username):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="测试账户不允许直接发电，请填写正式账号")

    user = db.query(User).filter(User.username == username).first()
    if user and not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    guide_price: Optional[float] = None
    if payload.guide_token:
        try:
            guide_payload = _parse_payment_guide_token(payload.guide_token)
            if afdian_service.is_custom_id_expired(guide_payload):
                raise ValueError("guide_token expired")
            if str(guide_payload.get("username") or "").strip() != username:
                raise ValueError("username mismatch")
            guide_price = _safe_parse_amount(guide_payload.get("current_price"))
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"guide_token 无效: {exc}") from exc

    current_price, _ = _get_user_price_and_limit_rule(user)

    if payload.target_monthly_price is not None:
        plan_price = _normalize_plan_price(payload.target_monthly_price)
    elif payload.plan_price is not None:
        plan_price = _normalize_plan_price(payload.plan_price)
    elif payload.custom_price is not None and action in ("upgrade", "downgrade"):
        plan_price = _normalize_plan_price(payload.custom_price)
    else:
        plan_price = _normalize_plan_price(guide_price if guide_price is not None else current_price)

    if action == "renew" and user and plan_price > current_price + PRICE_EPSILON:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="续费不支持升档，请选择升级")
    if action == "upgrade" and user and plan_price <= current_price + PRICE_EPSILON:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="升级档位必须高于当前档位")
    if action == "downgrade":
        expired = False
        if user is not None:
            expired, _ = is_user_expired(user)
        if not _is_downgrade_allowed(user, expired, current_price):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前账号不支持降档")
        if user and plan_price >= current_price - PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="降档档位必须低于当前档位")

    donation_amount = round(plan_price * months, 2)
    if donation_amount <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="???????")

    custom_id = afdian_service.build_custom_id(
        username,
        password,
        action,
        extra_payload={
            "months": months,
            "plan_price": plan_price,
            "requested_amount": donation_amount,
            "guide_token": payload.guide_token,
            "target_monthly_price": plan_price,
        },
    )
    donation_url = afdian_service.build_donation_url(custom_id, donation_amount)

    pending = _ensure_order_record(db, order_no=None, out_trade_no=None, custom_id=custom_id)
    pending.remark = custom_id
    pending.username = username
    pending.action = action
    pending.amount = donation_amount
    pending.status = "pending"
    pending.updated_at = _utc_now_naive()
    db.commit()

    logger.info("[USER-CENTER] payment-link generated. username=%s action=%s", username, action)
    return {
        "donation_url": donation_url,
        "custom_id": custom_id,
        "remark": custom_id,
        "action": action,
        "months": months,
        "plan_price": plan_price,
        "custom_price": donation_amount,
    }


@router.post("/activate")
async def activate_order(payload: ActivateRequest, db: Session = Depends(get_db)):
    out_trade_no = (payload.out_trade_no or "").strip() or None
    remark_or_custom_id = (payload.custom_id or payload.remark or "").strip() or None
    if not out_trade_no and not remark_or_custom_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少订单备注/custom_id 或商家订单号")

    order, raw_query_payload = await afdian_service.find_order(out_trade_no, remark_or_custom_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未查询到对应订单")

    order_no = afdian_service.get_order_no(order) or None
    out_trade_no = afdian_service.get_out_trade_no(order) or out_trade_no
    custom_id = afdian_service.get_custom_id(order) or remark_or_custom_id
    if not custom_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单缺少custom_id/remark，无法激活")

    local_order = _ensure_order_record(db, order_no, out_trade_no, custom_id)
    if local_order.status == "activated":
        return {
            "message": "订单已激活，无需重复提交",
            "idempotent": True,
            "username": local_order.username,
            "out_trade_no": local_order.out_trade_no,
            "order_no": local_order.order_no,
            "action": local_order.action,
        }

    try:
        custom_payload = afdian_service.parse_custom_id(custom_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if afdian_service.is_custom_id_expired(custom_payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 已过期，请重新生成发电链接")

    username = str(custom_payload.get("username") or "").strip()
    password = str(custom_payload.get("password") or "")
    action = _normalize_action(str(custom_payload.get("action") or "").strip().lower())
    months = _normalize_months(custom_payload.get("months") or 1)
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 信息不完整")

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        user = User(
            username=username,
            password=hash_password(password),
            is_admin=False,
            is_active=True,
            limit_rule=settings.AFDIAN_DEFAULT_LIMIT_RULE,
        )
        db.add(user)
    elif not verify_password(password, user.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="custom_id 用户密码校验失败")

    amount = _extract_order_amount(order)
    if amount is None:
        amount = _safe_parse_amount(getattr(local_order, "amount", None))

    plan_price_raw = custom_payload.get("plan_price")
    if plan_price_raw is None and amount is not None:
        plan_price_raw = round(amount / max(1, months), 2)
    if plan_price_raw is None:
        plan_price_raw = _infer_plan_price_from_rule(user.limit_rule or settings.AFDIAN_DEFAULT_LIMIT_RULE)
    plan_price = _normalize_plan_price(float(plan_price_raw))

    if amount is None:
        amount = round(plan_price * months, 2)

    requested_amount_raw = custom_payload.get("requested_amount")
    requested_amount = _safe_parse_amount(requested_amount_raw)
    if requested_amount is None:
        requested_amount = round(plan_price * months, 2)
    if amount + PRICE_EPSILON < requested_amount:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单支付金额不足，无法完成激活")

    current_price, _ = _get_user_price_and_limit_rule(user)
    converted_remaining_days: Optional[float] = None

    if action == "renew":
        if plan_price > current_price + PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="续费不支持升级套餐，请选择升级操作")

        final_plan_price = current_price if user.id is not None else plan_price
        extend_days = int(settings.AFDIAN_RENEW_DAYS) * months
        user.expiration_date = _extend_date(user.expiration_date, extend_days)
        user.limit_rule = _build_limit_rule_for_plan(user.limit_rule or settings.AFDIAN_DEFAULT_LIMIT_RULE, final_plan_price)
        user.boost_expiration_date = None
        user.boost_limit_rule = None
        user.is_active = True
    elif action == "upgrade":
        if amount is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="订单金额解析失败，无法计算提额档位")
        if plan_price <= current_price + PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="升级套餐必须高于当前套餐，不支持降级或同级升级")

        now_utc = _utc_now_naive()
        current_expiration = _normalize_naive_utc(user.expiration_date) or now_utc
        remaining_seconds = max(0.0, (current_expiration - now_utc).total_seconds())
        converted_seconds = remaining_seconds * (current_price / plan_price)
        converted_remaining_days = round(converted_seconds / 86400.0, 2)
        purchased_days = int(settings.AFDIAN_RENEW_DAYS) * months

        user.expiration_date = now_utc + timedelta(seconds=converted_seconds) + timedelta(days=purchased_days)
        user.limit_rule = _build_limit_rule_for_plan(user.limit_rule or settings.AFDIAN_DEFAULT_LIMIT_RULE, plan_price)
        user.boost_expiration_date = None
        user.boost_limit_rule = None
        user.is_active = True

    elif action == "downgrade":
        expired, _ = is_user_expired(user)
        if not _is_downgrade_allowed(user, expired, current_price):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前账号不支持降档")
        if plan_price >= current_price - PRICE_EPSILON:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="降档档位必须低于当前档位")

        # Downgrade only changes limit rule; expiration remains as-is.
        user.limit_rule = _build_limit_rule_for_plan(user.limit_rule or settings.AFDIAN_DEFAULT_LIMIT_RULE, plan_price)
        user.boost_expiration_date = None
        user.boost_limit_rule = None
        user.is_active = True

    local_order.order_no = order_no
    local_order.out_trade_no = out_trade_no
    local_order.custom_id = custom_id
    local_order.remark = custom_id
    local_order.username = username
    local_order.action = action
    local_order.amount = amount
    local_order.status = "activated"
    local_order.activated_at = _utc_now_naive()
    local_order.updated_at = _utc_now_naive()
    local_order.raw_payload = json_dumps_safe(
        {
            "order": order,
            "query_payload": raw_query_payload,
            "custom_payload": custom_payload,
            "months": months,
            "plan_price": plan_price,
            "requested_amount": requested_amount,
            "limit_rule": user.limit_rule,
            "converted_remaining_days": converted_remaining_days,
        }
    )

    db.commit()

    logger.info(
        "[USER-CENTER] order activated. username=%s action=%s out_trade_no=%s",
        username,
        action,
        out_trade_no,
    )
    return {
        "message": "激活成功",
        "idempotent": False,
        "username": username,
        "out_trade_no": out_trade_no,
        "order_no": order_no,
        "action": action,
        "amount": amount,
        "months": months,
        "plan_price": plan_price,
        "limit_rule": user.limit_rule,
        "converted_remaining_days": converted_remaining_days,
        "new_expiration_utc": _to_utc_iso(user.expiration_date),
        "boost_expiration_utc": _to_utc_iso(getattr(user, "boost_expiration_date", None)),
    }


def json_dumps_safe(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"
