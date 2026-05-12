# NeoData 股票/指数/板块查询服务 - 完整参考

## 请求参数详细说明

```json
{
    "channel": "openclaw",      // 渠道信息，可不填
    "sub_channel": "",          // 子渠道信息，可不填
    "query": "给我腾讯控股的公司简介、上市信息、主营业务、行业与概念归属", // 必填
    "request_id": "512b684a...", // 请求唯一ID，必填，用于链路追踪
    "se_params": {},            // 搜索引擎预留参数，可不填
    "extra_params": {}          // 扩展预留参数，可不填
}
```

## 响应字段完整说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | string | 状态码，`"200"` 表示成功 |
| `msg` | string | 状态描述 |
| `traceId` | string | 链路追踪 ID，可能为空 |
| `suc` | boolean | 是否成功 |
| `data.request_id` | string | 请求 ID |
| `data.apiData` | object | 结构化 API 召回结果 |
| `data.apiData.entity` | array\<object\> | 命中的标的列表 |
| `data.apiData.entity[].name` | string | 标的代码（如 `00700.HK`） |
| `data.apiData.entity[].code` | string | 标的名称（如 `腾讯控股`） |
| `data.apiData.apiRecall` | array\<object\> | API 内容块列表 |
| `data.apiData.apiRecall[].type` | string | 数据类型描述 |
| `data.apiData.apiRecall[].desc` | string | 类型描述 |
| `data.apiData.apiRecall[].content` | string | 具体内容文本 |
| `data.apiData.apiRecall[].tag` | string | 数据来源标签（如 `股票数据库`） |
| `data.docData` | object\|null | 金融类文本召回结果，包含与查询相关的财经资讯、研报、公告等文章内容；可能为 null |
| `data.docData.docRecall` | array\<object\> | 文档召回分组，按扩展检索词聚合 |
| `data.docData.docRecall[].extQuery` | string | 扩展检索词 |
| `data.docData.docRecall[].docList` | array\<object\> | 金融文章列表（财经资讯、券商研报、公司公告等） |
| `data.docData.docRecall[].docList[].docId` | string | 文章唯一 ID |
| `data.docData.docRecall[].docList[].title` | string | 文章标题 |
| `data.docData.docRecall[].docList[].publishTime` | number | 发布时间（Unix 时间戳，秒） |
| `data.docData.docRecall[].docList[].source` | string | 文章来源（媒体/机构） |
| `data.docData.docRecall[].docList[].url` | string | 文章链接 |
| `data.docData.docRecall[].docList[].content` | string | 文章正文或摘要 |
| `data.se_params` | object | 搜索引擎预留参数 |
| `data.extra_params` | object | 扩展预留参数 |

## 错误码

| code | msg | 说明 |
|------|-----|------|
| `1001` | 未命中意图 | 请求内容未识别到可处理的业务意图 |
| `1616039101` | 参数值不合法 | 入参校验失败 |
| `1006` | 查询解析拒答 | 查询在解析阶段被拒绝（如策略拦截、风险或不支持场景） |

## 响应示例

<details>
<summary>点击展开完整响应示例</summary>

```json
{
    "code": "200",
    "msg": "操作成功",
    "traceId": null,
    "data": {
        "request_id": "5c3b9d0ddd216b6099c5f826ffae6223",
        "apiData": {
            "entity": [
                { "name": "00700.HK", "code": "腾讯控股" },
                { "name": "TCTZF.PS", "code": "腾讯控股" },
                { "name": "TCEHY.PS", "code": "腾讯控股(ADR)" }
            ],
            "apiRecall": [
                {
                    "type": "公司概况与所属行业信息",
                    "desc": "公司概况与所属行业信息",
                    "content": "腾讯控股公司（股票代码：00700.HK），于2004-06-16在港股上市...",
                    "tag": "股票数据库"
                },
                {
                    "type": "主营构成与业绩趋势",
                    "desc": "主营构成与业绩趋势",
                    "content": "根据2025三季报，腾讯控股公司（股票代码:00700.HK），主营业务构成：...",
                    "tag": "股票数据库"
                }
            ]
        },
        "docData": {
            "docRecall": [
                {
                    "extQuery": "腾讯控股",
                    "docList": [
                        {
                            "type": "本地资讯",
                            "docId": "SN20250221154216abd73c41",
                            "publishTime": 1740122765,
                            "url": "http://gu.qq.com/...",
                            "title": "文章标题示例",
                            "content": "文章正文或摘要...",
                            "site": "21世纪经济报道",
                            "source": "21世纪经济报道"
                        }
                    ]
                }
            ]
        },
        "se_params": {},
        "extra_params": {}
    },
    "suc": true
}
```

</details>
