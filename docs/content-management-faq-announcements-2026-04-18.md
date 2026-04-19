# FAQ 与公告内容管理改动说明

日期：2026-04-18

## 本次新增能力

本次新增了两类可在管理员后台维护的内容：

1. 公告
2. FAQ

其中公告又分为两种展示方式：

1. `banner`
   作为普通消息公告，显示在控制台页面顶部。
2. `modal`
   作为弹窗公告，进入控制台后会弹出，单条公告在同一浏览器里手动关闭后不再重复弹出。

## 后端实现

仓库：

- [VoiceLinkServer1](D:/git/gitlab/VoiceLinkServer1)

新增数据表：

1. `announcement_entry`
2. `faq_entry`

主要字段说明：

- `title`
- `content`
- `sort_order`
- `is_published`
- `published_at`
- `updated_by_user_id`
- `updated_by_username`
- `created_at`
- `updated_at`

公告额外字段：

- `display_mode`
  可选 `banner` / `modal`

新增管理接口：

- `GET /v2/adminapi/content/announcements`
- `POST /v2/adminapi/content/announcements`
- `PATCH /v2/adminapi/content/announcements/{id}`
- `DELETE /v2/adminapi/content/announcements/{id}`
- `GET /v2/adminapi/content/faqs`
- `POST /v2/adminapi/content/faqs`
- `PATCH /v2/adminapi/content/faqs/{id}`
- `DELETE /v2/adminapi/content/faqs/{id}`

新增控制台读取接口：

- `GET /v2/api/content/announcements`
- `GET /v2/api/content/faqs`

说明：

- 以上控制台接口目前要求登录后访问
- FAQ 与公告都只返回 `is_published = true` 的内容

## 官网 / 控制台实现

仓库：

- [vrclswebsite](D:/git/gitlab/vrclswebsite)

已新增内容：

1. 控制台左侧导航新增 `常见问题`
2. 控制台新增 FAQ 页面
3. FAQ 页面按条展示已发布 FAQ，支持展开查看正文
4. 控制台全局增加普通公告区
5. 控制台全局增加弹窗公告展示

当前控制台公告行为：

1. 普通公告会显示在控制台页面内容区顶部
2. 弹窗公告会在控制台页面打开时显示
3. 弹窗公告关闭状态保存在浏览器 `localStorage`
4. 同一条弹窗公告在同一浏览器中关闭后不会重复弹出
5. 新公告因为 `id` 不同，会再次弹出

## 管理后台实现

仓库：

- [vrclswebsite](D:/git/gitlab/vrclswebsite)

已新增后台页签：

1. `内容`

内容页中包含两块：

1. 公告管理
2. FAQ 管理

当前后台操作能力：

1. 列表查看
2. 新建
3. 编辑
4. 删除
5. 立即发布 / 下线
6. 排序值调整

公告支持选择展示类型：

1. 普通消息
2. 弹窗公告

## 测试服务器状态

测试服务器：

- `root@192.168.2.53`

预览后端：

- `http://127.0.0.1:18980`

当前状态：

1. 预览后端已重新部署
2. `announcement_entry` / `faq_entry` 表已创建
3. 静态站点已重新同步
4. FAQ 页面和内容管理后台代码已部署到测试站

已确认：

- `GET /v2/adminapi/auth/state` 返回 `200`

未做的自动化验证：

1. 管理员真实登录后新增 FAQ
2. 管理员真实登录后发布公告
3. 控制台登录后查看发布结果

原因：

- 当前对话中没有可直接使用的管理员登录凭据，因此没有执行真实发布流转

## 当前建议

建议你下一步直接在测试环境里做一轮人工验收：

1. 登录管理员后台
2. 在 `内容` 页签中新建一条普通公告
3. 新建一条弹窗公告
4. 新建 2 到 3 条 FAQ
5. 登录控制台确认：
   - 顶部普通公告是否显示
   - 弹窗公告是否弹出
   - FAQ 页面是否可见并可展开正文
