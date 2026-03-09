# "下月限速规则自动轮转"功能修改方案

## 1. 需求描述

当前系统的每日限速规则 `limit_rule`（如 `"1500/day"`）是固定的，管理员手动修改后立即生效。

新需求：支持**预设下个月的每日限速规则**，到了下个月1号自动切换。例如：
- 3月：每天 1500 次 (`limit_rule = "1500/day"`)
- 4月：每天 3000 次 (`next_limit_rule = "3000/day"` -> 4月1号自动变为 `limit_rule`)

**限速机制本身不变**，仍然是每日上限 + 分钟级限速，只是 `limit_rule` 的值可以按月自动更新。

---

## 2. 当前系统现状

### 2.1 数据模型 (`src/db/models.py`)
```python
class User(Base):
    limit_rule = Column(String(100), nullable=True)          # 当前限速规则，如 "1500/day"
    boost_limit_rule = Column(String(100), nullable=True)     # 临时提速规则
    boost_expiration_date = Column(DateTime, nullable=True)   # 提速过期时间
```

### 2.2 限速规则获取 (`src/core/dependencies.py`)
```python
def get_effective_user_limit_rule(user: User) -> str:
    # 优先级：boost_limit_rule（未过期）> limit_rule > 全局默认值
    default_rule = settings.AFDIAN_DEFAULT_LIMIT_RULE or "800/day;8/minute"
    if boost未过期 and boost_rule不为空:
        return boost_rule
    return user.limit_rule or default_rule
```

### 2.3 限速执行 (`src/core/rate_limiter.py`)
- 使用 `limits` 库的 `MovingWindowRateLimiter` 滑动窗口算法
- key 按天生成：`username:2026-03-07`
- 解析 `limit_rule` 字符串（如 `"1500/day;8/minute"`）为多条限速规则
- **这部分逻辑完全不需要修改**

### 2.4 定时任务 (`src/main.py`)
- 已有 APScheduler，每天凌晨0点检查用户过期
- 已有每周一凌晨3点更新 filter 配置

### 2.5 管理接口 (`src/routers/manage_api.py`)
- `POST /manageapi/addUser`：创建用户时可设置 `limit_rule`
- `POST /manageapi/updateUser`：更新用户时可修改 `limit_rule`

### 2.6 用户状态接口 (`src/routers/api.py`)
- `GET /api/account/status`：返回用户到期时间、boost 信息
- `GET /api/account/usage`：返回每日用量统计、`limit_rule`、`daily_limit`

---

## 3. 修改方案

### 3.1 数据库变更

#### 3.1.1 User 表新增字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `next_limit_rule` | `String(100)`, nullable | `NULL` | 下月生效的每日限速规则，如 `"3000/day"` |

#### 3.1.2 ORM 模型修改

**文件**: `src/db/models.py`

```python
class User(Base):
    __tablename__ = "user"
    # ... 现有字段 ...
    next_limit_rule = Column(String(100), nullable=True)  # 新增：下月自动生效的限速规则
```

#### 3.1.3 数据库迁移

**文件**: `src/main.py` 的 `check_and_update_db()` 函数

在现有的 user 表列检查逻辑中追加：

```python
if 'next_limit_rule' not in user_columns:
    db.execute(text('ALTER TABLE user ADD COLUMN next_limit_rule VARCHAR(100)'))
    logger.info("Added next_limit_rule to user table")
```

---

### 3.2 定时任务：月度轮转

**文件**: `src/main.py`

#### 3.2.1 新增轮转函数

```python
def rotate_monthly_limit_rules():
    """
    每月1号执行：将 next_limit_rule 覆盖到 limit_rule，然后清空 next_limit_rule。
    """
    db = SessionLocal()
    try:
        # 只查询 next_limit_rule 不为空的用户
        users = db.query(User).filter(
            User.next_limit_rule != None,
            User.next_limit_rule != "",
        ).all()

        rotated_count = 0
        for user in users:
            old_rule = user.limit_rule
            user.limit_rule = user.next_limit_rule
            user.next_limit_rule = None
            rotated_count += 1
            logger.info(
                f"[LIMIT-ROTATE] 用户 {user.username} 限速规则已轮转: "
                f"'{old_rule}' -> '{user.limit_rule}'"
            )

        db.commit()
        logger.info(f"[LIMIT-ROTATE] 月度限速规则轮转完成，共处理 {rotated_count} 个用户")
    except Exception as e:
        db.rollback()
        logger.error(f"[LIMIT-ROTATE] 月度限速规则轮转失败: {e}")
    finally:
        db.close()
```

#### 3.2.2 注册定时任务

在 `lifespan()` 函数中，现有的 `scheduler.add_job(...)` 后面追加：

```python
# 每月1号凌晨 00:00:30 UTC 执行限速规则轮转（比过期检查晚30秒，避免并发）
scheduler.add_job(rotate_monthly_limit_rules, 'cron', day=1, hour=0, minute=0, second=30)
```

---

### 3.3 Pydantic Schema 变更

**文件**: `src/schemas/user.py`

#### 3.3.1 UserBase 新增字段

```python
class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    is_admin: bool = False
    is_active: bool = True
    limit_rule: Optional[str] = None
    next_limit_rule: Optional[str] = None  # 新增
    expiration_date: Optional[datetime] = None
```

#### 3.3.2 UserUpdate 新增字段

```python
class UserUpdate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: Optional[str] = Field(None, min_length=6)
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    limit_rule: Optional[str] = None
    next_limit_rule: Optional[str] = None  # 新增
    expiration_date: Optional[datetime] = None
```

---

### 3.4 管理接口变更

**文件**: `src/routers/manage_api.py`

#### 3.4.1 addUser 接口

在创建用户时支持传入 `next_limit_rule`：

```python
new_user = User(
    username=user_data.username,
    password=hashed_password,
    is_admin=user_data.is_admin,
    is_active=user_data.is_active,
    limit_rule=user_data.limit_rule,
    next_limit_rule=user_data.next_limit_rule,  # 新增
    expiration_date=user_data.expiration_date
)
```

#### 3.4.2 updateUser 接口

在更新用户时支持修改 `next_limit_rule`：

```python
if user_data.next_limit_rule is not None:
    user_to_update.next_limit_rule = user_data.next_limit_rule
```

---

### 3.5 用户状态接口变更

**文件**: `src/routers/api.py`

#### 3.5.1 /api/account/status 接口

在返回中新增 `next_limit_rule` 字段：

```python
return {
    "username": current_user.username,
    "is_active": bool(current_user.is_active),
    "is_expired": is_expired,
    "expiration_utc": expiration_utc,
    "remaining_seconds": remaining_seconds,
    "boost_expiration_utc": boost_expiration_utc,
    "boost_remaining_seconds": boost_remaining_seconds,
    "boost_limit_rule": current_user.boost_limit_rule,
    "next_limit_rule": current_user.next_limit_rule,  # 新增：下月限速规则
}
```

#### 3.5.2 /api/account/usage 接口

在返回中新增 `next_limit_rule` 和 `next_daily_limit` 字段：

```python
# 解析下月的每日上限数字（供客户端展示用）
next_limit_rule = current_user.next_limit_rule or ""
next_daily_limit = _extract_daily_limit(next_limit_rule) if next_limit_rule else None

return {
    # ... 现有字段 ...
    "limit_rule": limit_rule,
    "daily_limit": daily_limit,
    "next_limit_rule": next_limit_rule or None,        # 新增
    "next_daily_limit": next_daily_limit,               # 新增
    # ... 其余字段 ...
}
```

---

## 4. 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `src/core/rate_limiter.py` | 限速执行逻辑不变，仍然读取 `limit_rule` 做滑动窗口 |
| `src/core/dependencies.py` | `get_effective_user_limit_rule()` 不变，仍然按 boost > limit_rule > 默认值 的优先级 |
| `src/core/config.py` | 不需要新增配置项 |

---

## 5. 改动文件汇总

| # | 文件路径 | 改动内容 | 改动量 |
|---|---------|---------|--------|
| 1 | `src/db/models.py` | User 表新增 `next_limit_rule` 字段 | 1 行 |
| 2 | `src/main.py` | 数据库迁移 + 新增轮转定时任务 | ~25 行 |
| 3 | `src/schemas/user.py` | UserBase 和 UserUpdate 新增 `next_limit_rule` | 2 行 |
| 4 | `src/routers/manage_api.py` | addUser/updateUser 支持 `next_limit_rule` | 3 行 |
| 5 | `src/routers/api.py` | status/usage 接口返回 `next_limit_rule` | ~6 行 |

**总改动量：约 37 行代码**

---

## 6. 运行时行为说明

### 6.1 场景示例

管理员在3月15日为用户 `alice` 设置：
```json
{
    "username": "alice",
    "limit_rule": "1500/day",
    "next_limit_rule": "3000/day"
}
```

| 时间点 | `limit_rule` | `next_limit_rule` | 每日实际上限 |
|--------|-------------|-------------------|-------------|
| 3月15日 ~ 3月31日 | `"1500/day"` | `"3000/day"` | 1500次/天 |
| 4月1日 00:00:30 定时任务执行后 | `"3000/day"` | `NULL` | 3000次/天 |
| 4月1日之后 | `"3000/day"` | `NULL` | 3000次/天（保持不变） |

### 6.2 轮转后 next_limit_rule 被清空

轮转执行后 `next_limit_rule` 会被设为 `NULL`，这意味着：
- 如果不再设置新的 `next_limit_rule`，用户的 `limit_rule` 将一直保持为轮转后的值
- 如果需要每月都变化，管理员需要在下个月之前再次设置 `next_limit_rule`

### 6.3 与 boost 机制的关系

优先级保持不变：`boost_limit_rule（未过期）> limit_rule > 全局默认值`

- `next_limit_rule` 只影响 `limit_rule` 的值
- boost 期间即使发生了月度轮转，boost 仍然优先
- boost 过期后，会自动回落到新的 `limit_rule`（已经是轮转后的值）

### 6.4 边界情况处理

| 情况 | 处理方式 |
|------|---------|
| `next_limit_rule` 为空或 NULL | 不参与轮转，`limit_rule` 不变 |
| `next_limit_rule` 格式错误（如 `"abc"`） | 照样轮转到 `limit_rule`，由限速解析时打日志跳过 |
| 服务器在月初重启错过定时任务 | APScheduler 默认不补执行错过的 job，需要下个月才会轮转（可后续考虑加 `misfire_grace_time`） |
| 管理员在月初轮转执行前修改了 `next_limit_rule` | 以轮转时刻的值为准 |
