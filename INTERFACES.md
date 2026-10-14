# 接口契约说明 v0.1

类型定义见 [types.ts](types.ts)，本文件解释"为什么这么切"和"实现时要注意什么"。

## 一、管道

```
RawInput ──Adapter.ingest──► ActivityRecord[] ──Calculator──► EmissionRecord[]
                                                                    │
                                                              Ledger ▼
                                                          CarbonLedger
                                                                    │
                                                            Exporter ▼
                                                          CarbonPassport
```

一次活动的完整链路：

```
账单里一行「滴滴出行 23.50 元」
  → ActivityRecord{ category:'transport', quantity:8.2, unit:'km', confidence:0.6 }
  → EmissionRecord{ kgCO2e:1.57, scope:3, factorId:'cn_didi_car' }
  → CarbonLedger.byCategory.transport += 1.57
  → CarbonPassport.entries[].kgCO2e = 1.57
```

## 二、L1 采集：CarbonAdapter

```ts
interface CarbonAdapter {
  capability: AdapterCapability;
  ingest(input: RawInput): Promise<ActivityRecord[]>;
}
```

规则：

- **只归一化，不核算**。算排放是内核的事，适配器算了自己的因子就跟别人对不齐。
- **保留 raw**。原始行、OCR 原文都塞进 `raw`，算错了能人工复核。
- **诚实标 confidence**。OCR 读出来的电量、按金额反推的里程，都不是 1.0。
- **一个适配器只认一类素材**。账单 CSV、电费图片、手输表单，各写各的。
- 幂等：同一份素材重复导入要能去重（用 `sourceId + occurredAt + rawRowHash` 做 key）。

## 三、L2 归一：ActivityRecord

系统里只有这一种原子记录，界面、核算、导出全从它出发。

- `category` 只有那十种。归类不了就 `other`，别新造。
- `quantity + unit` 必须是明确物理量。**能拿到实际里程就别用金额反推**；
  只能拿到金额时（比如"购物 59.90 元"），用支出强度因子，并在 `raw` 里写明是金额推算的。
- `confidence` 决定界面上要不要标"待复核"。低于 0.7 的建议高亮。

## 四、L3 核算：EmissionFactor

因子库独立于代码，用 CSV/JSON 维护，能整体换版本（`methodology.factorsVersion` 记下来）。

- 每条因子必须带 `source`（依据出处）和 `year`。
- `scope` 决定这条排放记到 Scope 1/2/3：
  - 1 = 自己烧的（燃气、汽油）
  - 2 = 买来的电（电网因子）
  - 3 = 供应链与消费（出行、外卖、快递、购物）
- 匹配规则：`category + unit + region + year` 命中，取最近年份；命中不了就报错，别默认 0。

## 五、L4 记账：CarbonLedger

- `totals` 三个范围 + 合计；`byCategory` 分类目；
- 每条 `EmissionRecord` 必须能回到 `activityId` 和 `factorId`；
- 同一批数据重复核算结果要一致（纯函数，无隐藏状态）。

## 六、L5 出具：CarbonPassport

这是"互通层"，也是本项目的立论点：数据可以各自为政，但**凭证格式必须统一**。

- `specVersion` 固定字符串，方便以后加版本；
- `entries` 保留到单条活动粒度，别人拿到能做二次核算；
- `evidenceHashes`：对原始记录做哈希，回答"凭什么信你这份账"（对应截图里提到的数据确权 / 区块链存证方向）；
- 一个 JSON 文件，谁都能读——这就是"统一数据格式"的最小可行版本。

## 七、平台接入清单（首批）

| 平台 / 来源 | 能拿到什么 | 怎么接 | 状态 |
|---|---|---|---|
| 支付宝账单 | 出行、外卖、水电煤、购物（按笔） | 用户导出 CSV | 计划 connected |
| 微信支付账单 | 同上 | 用户导出 CSV | 计划 connected |
| 电费 / 燃气单据 | 用电量、燃气量 | 拍照 + 多模态 OCR | 计划 connected |
| 高德 / 滴滴行程 | 实际里程 | 用户截图 / 导出，OCR 兜底 | planned |
| 微信运动 / 手环 | 步数、骑行 | 用户截图，OCR | planned |
| 各行碳账户 | 减排量（口径不明） | 无接口，等开放 | planned |
| 各地碳普惠平台 | 减排量、碳积分 | 无接口，等开放 | planned |
| 蚂蚁森林 | 减排量、树 | 无接口，等开放 | planned |

下三行就是"数据孤岛"本身。本项目不假装能接，而是把接口摆在那儿等它们开放——
这个姿态比硬爬数据更站得住，也更贴赞助方的立场。
