# 库街区公开接口实测记录

核实日期：2026-10-25（北京时间）。本记录对应核实时点，平台网页、字段或访问条件以后可能变化。

## 核实方式和范围

- 从库街区公开网页 HTML 找到实际加载的 JavaScript，核对网页使用的只读请求。
- 对以下接口发起少量匿名请求，未提供登录 token、Cookie 或已有账号的设备标识。
- 未执行登录、发帖、评论、点赞、签到等操作；未处理验证码或绕过访问限制。
- 原始公开响应只用于本地核实，位于工作区 `tmp/kuro-api-research/`，不作为项目示例数据发布。
- 本文只记录结构和聚合核实结果，不包含真实评论全文、昵称、用户 ID 或头像地址。

## 公共请求形状

API 根地址：`https://api.kurobbs.com`。

核实成功的读取请求使用 `POST`，请求体为 `application/x-www-form-urlencoded;charset=utf-8`。实测请求头：

```text
source: h5
version: 3.3.2
Origin: https://www.kurobbs.com
Referer: https://www.kurobbs.com/ 或对应公开帖子 URL
User-Agent: 普通桌面浏览器 User-Agent
```

这些是成功请求的记录，不表示每一个头都是必要条件，也不保证未来仍可匿名读取。官方网页当前脚本会从本地登录状态取 token；访客状态下 token 为空。客户端应将业务 `code != 200` 和 HTTP 异常视为失败，并明确提示，不能把失败当成“无评论”。

官方响应成功形状为 `{"code":200,"data":{...},"msg":"请求成功","success":true,...}`。HTTP 200 本身不代表业务成功。

## 1. 一级评论分页

路径：`/forum/comment/getPostCommentListV2`。

官网当前实际参数：

| 参数 | 实测值/意义 |
| --- | --- |
| `postId` | 字符串帖子 ID |
| `pageIndex` | 从 1 开始的页码 |
| `pageSize` | 实测 5；官网分页组件可使用 50 |
| `showOrderType` | `2` 为“默认”，`3` 为“最新”，由官网下拉菜单映射确认 |
| `isOnlyPublisher` | `0` 全部评论，官网切换“只看楼主”时设为 `1` |

官网此处没有传 `sortType` 或 `gameId`，项目应优先遵循官网当前形状。第三方客户端中的 `sortType` 不应直接当成官网参数。

`data` 核实字段：

- `postCommentList`：普通一级评论数组。
- `stickComments`：置顶评论数组。
- `hotComments`：官网会消费此字段，但本次“最新”请求未返回；应作为可选字段。
- `hasNext`：实测数字 `1` / `0`。
- `hasFilter`：可选布尔值，表示平台过滤信息；不应据此推测被过滤评论的内容。

一级评论核实字段：

| 字段 | 类型/处理建议 |
| --- | --- |
| `commentId` | 字符串；作为去重主键 |
| `commentContent` | 富文本块数组，见后文 |
| `commentTime` | 展示时间字符串；本次既有完整年月日，也有 `10-22` 等无年份日期，不能保证精确时间戳 |
| `floor` | 楼层数字 |
| `likeCount` | 点赞数量，可能缺失时应保留未知状态 |
| `replyCount` | 楼中楼数量 |
| `replyVos` | 内联回复预览；不保证完整 |
| `userId` | 字符串，仅在本地用于匿名化/去重；默认输出无需保留原值 |
| `isOfficial` / `isPublisher` | 官方/楼主标识 |
| `ipRegion`、`userName`、`userHeadUrl` 等 | 可能出现；研究所需字段之外无需留存 |

### 分页边界实测

公开测试帖的第 1 页在 `pageSize=5` 时只返回 2 条一级评论，同时 `hasNext=1`；第 2 页返回空数组及 `hasNext=0`。

因此不能仅用“条数小于 pageSize”终止。建议综合 `hasNext`、空页、没有新 ID 的重复页及用户设置的页数上限终止，并记录实际停止原因。评论区在采集期间可能变化，按页采集不能保证快照一致性。

## 2. 楼中楼分页

路径：`/forum/comment/getReplyList`。

实测请求参数：`postId`、`postCommentId`、`pageIndex`、`pageSize`。

第三方一手客户端实现了该路径，本地匿名实测也成功，返回：

- `data.replys`：回复数组，字段名称确实是 `replys`。
- `data.hasNext`：数字分页标记。
- `data.postComment`：父评论对象。
- `gameId`、`gameForumId`、`postType` 等上下文信息。

回复核实字段：

| 字段 | 说明 |
| --- | --- |
| `replyId` | 回复字符串 ID，与顶级评论使用不同类型前缀构成输出键 |
| `postCommentId` | 父级一级评论 ID |
| `postCommentReplayId` | 内联预览可能返回，注意平台拼写为 `Replay`；通常指被回复的楼中楼 |
| `replyContent` | 富文本块数组 |
| `replyContentStr` | 内联预览可能返回的文本备用字段；独立分页不保证返回 |
| `createTime` | 本次返回毫秒 Unix 时间戳 |
| `replyTime` | 展示时间字符串 |
| `likeCount` | 内联预览有返回，独立回复分页样例缺失，不能强行补成已知的 0 |
| `userId` / `toUserId` | 回复者及被回复者 ID，默认研究输出匿名化或省略 |

内联 `replyVos` 和独立分页 `replys` 会重复同一回复；应按帖子和回复 ID 去重，且不能把内联预览当作全部楼中楼。

## 3. 富文本提取

本次真实结构包含以下几类：

```json
[
  {"contentType": 1, "content": "示意文本"},
  {"contentType": 1, "content": "同一段文字", "children": [{"type": 1, "content": "同一段文字"}]},
  {"contentType": 2, "url": "https://example.invalid/image.jpg", "imgHeight": 100, "imgWidth": 100}
]
```

以上为人工编写的结构示意，不是真实玩家评论。提取时同一个文本块优先取 `children` 递归文本，只有取不到文本才回退 `content`，避免父子文字重复。保留必要换行，不把图片 URL、头像 URL 当成评论文字。纯图片评论可以标记无可分析文本，不能硬判正面/负面。

旧评论中可出现 `_[/表情名称]` 等表情占位符。应保留原始内容的本地副本并在清洗时单独处理；游戏名、角色名、否定词和玩家术语不能一概当停用词删除。

帖子、评论、回复和用户 ID 可能超过 JavaScript 安全整数范围，所有环节应按字符串处理。

## 4. 官方公告发现（已实测成功）

路径：`/forum/companyEvent/findEventList`。

实测参数：

| 参数 | 值/意义 |
| --- | --- |
| `gameId` | `3` 为鸣潮，`2` 为战双帕弥什 |
| `eventType` | 官网枚举 `1` 活动、`2` 资讯、`3` 公告；本次实测 `3` |
| `pageNo` | 从 1 开始，注意不是评论接口的 `pageIndex` |
| `pageSize` | 实测 5 和 30 |

响应 `data.list` 含 `postId`、`postTitle`、`publishTime` 等；`publishTime` 实测为毫秒时间戳。公告条目自身的 `id` 与真正的 `postId` 不相同，构造帖子 URL 时必须取 `postId`。公告结果没有保证提供评论数量。

### 本次发现的研究候选帖

| 主题 | 标题 | 公开 URL | 验证状态 |
| --- | --- | --- | --- |
| 角色反馈 | [余心所向九死未悔]角色活动唤取 | https://www.kurobbs.com/mc/post/1562064227775750144 | 公告发现成功；匿名读取一级评论第 1 页成功，返回 5 条及 `hasNext=1` |
| 版本优化 | 3.7版本已知问题及更新说明 | https://www.kurobbs.com/mc/post/1554788184571133952 | 公告发现成功；匿名读取一级评论第 1 页成功，返回 5 条及 `hasNext=1` |
| 客户端优化 | 关于客户端资源分级功能的相关说明 | https://www.kurobbs.com/mc/post/1552747271999410176 | 公告发现成功；评论可用性以实际采集结果为准 |

发现日期均为 2026-10-25。标题和版本号是核实时 API 返回值，不能据此推断所有游戏用户的意见。角色公告的发布时间戳换算为北京时间 2026-10-21。

## 5. 未验证成功的功能

官网脚本出现 `/forum/search/v2/join`、`/forum/search/v2/post`。本次使用关键词“优化”的匿名小样本请求均收到业务 `code=102`、`msg=服务器外部错误`；`/forum/list` 的一组匿名参数也返回相同错误。对角色候选帖调用 `/forum/getPostDetail`，使用 `postId` 和 `reqSource=1` 也得到业务 `102`。这些错误不能直接认定为“需要登录”，也不能据此声称全文搜索或详情接口已通过验证。

项目应交付已验证的官方公告发现及指定公开帖子链接采集；标题可以使用公告发现结果或用户提供值。可选详情元数据读取失败不应阻断已验证可用的评论采集。不宣传未通过实测的全文搜索能力，不通过猜测签名、伪造凭证或规避限制继续访问。

## 一手核实来源

- [库街区公开首页](https://www.kurobbs.com/mc/home/9)：本次返回的 HTML 包含以下实际脚本地址。
- [官网帖子详情脚本](https://web-static.kurobbs.com/resource/prod/assets/post-detail-DP5kdU8b.js)：评论路径、`showOrderType` 菜单映射、只看楼主参数、置顶/热评处理。
- [官网请求封装与接口脚本](https://web-static.kurobbs.com/resource/prod/assets/useConfig-DLqk4N_9.js)：API 根地址、表单请求和访客 token 处理、官方公告接口与枚举。
- [官网分页组件](https://web-static.kurobbs.com/resource/prod/assets/useListRequester-Ci39_XYq.js)：`pageIndex`、`pageSize` 和 `hasNext`。
- [官网搜索脚本](https://web-static.kurobbs.com/resource/prod/assets/search-container-Sxg9sHGO.js)：搜索路径存在的依据，不是成功访问的证据。
- [FoundryWP/KuroBBS 的 KuroForumService.cs](https://github.com/FoundryWP/KuroBBS/blob/main/KuroBBS/KuroBBS/KuroBBS.Shared/Services/KuroForumService.cs)：第三方客户端的一手实现，交叉核对评论和回复字段；实际参数以官网和本次实测为准。
- [TomyJan/Kuro-API-Collection](https://github.com/TomyJan/Kuro-API-Collection/blob/master/API/forum/getPostDetail.md)：旧版结构及公开测试帖的发现来源，不把其旧版登录要求直接套用到当前官网。

脚本文件名带构建哈希，下次部署后可能失效；重新核实时应从新的公开 HTML 读取实际链接。
