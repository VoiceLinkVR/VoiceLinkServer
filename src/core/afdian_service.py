import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


class AfdianService:
    def __init__(self) -> None:
        self._fernet = Fernet(self._build_fernet_key(settings.AFDIAN_CUSTOM_ID_KEY))

    @staticmethod
    def _build_fernet_key(secret: str) -> bytes:
        seed = (secret or "afdian-default-key").encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        return base64.urlsafe_b64encode(digest)

    @staticmethod
    def _to_compact_json(payload: Dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def build_custom_id(
        self,
        username: str,
        password: str,
        action: str,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload = {
            "v": 1,
            "username": username,
            "password": password,
            "action": action,
            "ts": int(time.time()),
            "nonce": uuid.uuid4().hex[:16],
        }
        if extra_payload:
            for key, value in extra_payload.items():
                if key in payload:
                    continue
                payload[key] = value

        token = self._fernet.encrypt(self._to_compact_json(payload).encode("utf-8"))
        return token.decode("utf-8")

    def parse_custom_id(self, custom_id: str) -> Dict[str, Any]:
        if not custom_id:
            raise ValueError("custom_id is empty")

        try:
            decoded = self._fernet.decrypt(custom_id.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("custom_id decrypt failed") from exc

        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("custom_id payload invalid")
        return payload

    def is_custom_id_expired(self, payload: Dict[str, Any]) -> bool:
        ttl = max(0, int(settings.AFDIAN_CUSTOM_ID_TTL_SECONDS))
        if ttl <= 0:
            return False

        ts = int(payload.get("ts") or 0)
        if ts <= 0:
            return True

        now_ts = int(datetime.now(timezone.utc).timestamp())
        return now_ts - ts > ttl

    def build_donation_url(self, custom_id: str, custom_price: Optional[float] = None) -> str:
        parsed = urlparse(settings.AFDIAN_ORDER_CREATE_BASE_URL)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        query["user_id"] = settings.AFDIAN_USER_ID
        # Prefer the dedicated custom_order_id field. Webhook activation will
        # query the order API again and read this field back from Afdian.
        query["custom_order_id"] = custom_id

        price = settings.AFDIAN_CUSTOM_PRICE if custom_price is None else custom_price
        query["custom_price"] = f"{max(0.01, float(price)):.2f}"

        encoded_query = urlencode(query)
        return urlunparse(parsed._replace(query=encoded_query))

    def _build_query_sign(self, params_json: str, ts: int) -> str:
        kv_string = f"params{params_json}ts{ts}user_id{settings.AFDIAN_USER_ID}"
        base = f"{settings.AFDIAN_TOKEN}{kv_string}"
        return hashlib.md5(base.encode("utf-8")).hexdigest()

    async def query_order(self, out_trade_no: Optional[str] = None, page: int = 1) -> Dict[str, Any]:
        if not settings.AFDIAN_TOKEN:
            raise ValueError("AFDIAN_TOKEN is empty")

        if out_trade_no:
            params: Dict[str, Any] = {"out_trade_no": out_trade_no}
        else:
            params = {"page": max(1, page)}

        params_json = self._to_compact_json(params)
        ts = int(time.time())
        sign = self._build_query_sign(params_json, ts)
        request_body = {
            "user_id": settings.AFDIAN_USER_ID,
            "params": params_json,
            "ts": ts,
            "sign": sign,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.AFDIAN_QUERY_ORDER_URL, json=request_body)
            response.raise_for_status()
            payload = response.json()

        if payload.get("ec") != 200:
            message = payload.get("em") or "query-order failed"
            raise ValueError(str(message))

        return payload

    @staticmethod
    def extract_orders(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("list", "order_list", "orders"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def extract_webhook_order(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        order = data.get("order")
        if isinstance(order, dict):
            return order

        orders = data.get("orders")
        if isinstance(orders, list):
            return next((item for item in orders if isinstance(item, dict)), None)

        return None

    @staticmethod
    def get_order_no(order: Dict[str, Any]) -> str:
        return str(order.get("order_no") or order.get("trade_no") or order.get("id") or "").strip()

    @staticmethod
    def get_out_trade_no(order: Dict[str, Any]) -> str:
        return str(order.get("out_trade_no") or order.get("out_order_no") or "").strip()

    @staticmethod
    def get_custom_id(order: Dict[str, Any]) -> str:
        return str(order.get("custom_order_id") or order.get("custom_id") or order.get("remark") or "").strip()

    @staticmethod
    def get_explicit_custom_id(order: Dict[str, Any]) -> str:
        return str(order.get("custom_order_id") or order.get("custom_id") or "").strip()

    @staticmethod
    def get_order_status(order: Dict[str, Any]) -> Optional[int]:
        raw_status = order.get("status")
        try:
            return int(raw_status)
        except (TypeError, ValueError):
            return None

    def is_paid_order(self, order: Dict[str, Any]) -> bool:
        return self.get_order_status(order) == 2

    async def find_order(
        self,
        out_trade_no: Optional[str],
        remark_or_custom_id: Optional[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if out_trade_no:
            payload = await self.query_order(out_trade_no=out_trade_no)
            orders = self.extract_orders(payload)
            if not orders:
                return None, payload

            matched = next(
                (item for item in orders if self.get_out_trade_no(item) == out_trade_no),
                orders[0],
            )
            return matched, payload

        target = (remark_or_custom_id or "").strip()
        if not target:
            return None, None

        max_pages = max(1, min(10, int(settings.AFDIAN_QUERY_SCAN_PAGES)))
        for page in range(1, max_pages + 1):
            payload = await self.query_order(page=page)
            orders = self.extract_orders(payload)
            if not orders:
                break

            for order in orders:
                if self.get_custom_id(order) == target:
                    return order, payload

        return None, None


afdian_service = AfdianService()
