# app：carbon-hub 的实现

零第三方依赖，只用 Python 标准库。要求 Python 3.11+（本机 3.13）。

## 怎么跑

```sh
cd app
python -m unittest discover -s tests -t .                      # 跑验收用例（31 个）
python -m carbonhub.cli                                        # 端到端演示（示例数据）
python -m carbonhub.cli --receipts ../fixtures/real_receipts_20261013.json   # 真实单据
python -m carbonhub.cli --images ../fixtures/samples/own/*.jpg  # 图片走多模态识别
```

图片识别需要环境变量（不写进任何文件）：

```sh
set CARBONHUB_VISION_API_KEY=你的Key
set CARBONHUB_VISION_BASE_URL=https://api.deepseek.com   # 可选
set CARBONHUB_VISION_MODEL=deepseek-flash                # 可选
```

识别结果会按图片内容哈希缓存到 `out/recognition_cache/`。没网或没 Key 时自动读缓存，
现场演示不会因为断网而中断。

## 除了算账，还能干什么

| 模块 | 命令 / 入口 | 说明 |
|---|---|---|
| 减排规划 | 界面「③ 减排规划」 | 先看用量画像，再在"预算 / 最多几项 / 门槛 / 单类别上限"四重约束下挑组合，附边际成本，并指出真正卡住的是哪条约束 |
| 数据质量分 | 界面「② 核算结果」下 | 把置信度、因子年份、完整性折成 0-100 分，并提示补什么能提分 |
| 凭证校验 | `python -m carbonhub.verify out/passport.json` | 独立校验，能验"内容有没有被改"，验不了"原件是不是真的" |
| 只读接口 | `python -m carbonhub.api` | 把账本、建议、凭证通过 HTTP 给第三方读，默认只绑本机 |
| 静态报告 | 界面「④ 导出」或 `python -m carbonhub.report` | 生成单个自包含 HTML，无外部依赖，微信发出去双击就能看 |
| 数据探查 | `python -m carbonhub.profile_data 数据.csv` | 拿到陌生数据几分钟看清字段、类型、缺失、时间跨度，支持 CSV/TSV/JSON 与 GBK |

凭证里带 `ledgerRoot`（对全部明细求的默克尔根）。改任何一个数字，重算的根就对不上，
`verify` 会直接指出来。

## 怎么让别人看到结果

| 办法 | 适合 | 怎么做 |
|---|---|---|
| 静态报告 | **最省事，首选** | 界面「④ 导出与校验」点"下载静态报告"，或 `python -m carbonhub.report`。单个 HTML，无外部依赖，微信发过去双击就能看，也能直接打印成 PDF |
| 同一 wifi | 朋友就在旁边 | 双击 `run-lan.bat`，把终端打印的 Network URL 发给他 |
| 跨网络 | 不在一个网段 | 用内网穿透（cloudflared / ngrok）或 Tailscale 组虚拟局域网。注意公网链接谁拿到都能看，页面上有你的户号地址 |

校园网普遍开客户端隔离，跨宿舍楼基本连不通，别在这上面浪费时间。

## 界面

双击 `run-local.bat` 就行（等价于下面这条命令）：

```sh
.venv\Scripts\streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

默认只绑本机，同一局域网里的别人打不开，适合自己开发和演示。

要让同网络的几台电脑打开（比如让队友或评委用手机看），双击 `run-lan.bat`，
它绑 `0.0.0.0`，启动后终端会打印一个 `Network URL`，把那串地址发给对方即可。
注意两点：只用可信网络（校园网、咖啡厅这种公共网络别开），演示完按 Ctrl+C 关掉。

界面只做流程编排和展示，所有计算都调 `carbonhub` 的函数，不重复实现业务逻辑。
依赖见 `requirements.txt`（只有界面需要，内核不用装任何东西）。

`cli` 会读 `../fixtures` 下的示例数据，把活动记录算成账本，并在 `out/passport.json`
写出一份碳护照。

## 结构

| 文件 | 作用 |
|---|---|
| `carbonhub/models.py` | 契约：受控词表 + 数据结构，非法值写入即报错 |
| `carbonhub/factors.py` | 因子库加载与匹配，匹配不到抛 `FactorNotFound`，绝不按 0 算 |
| `carbonhub/kernel.py` | 核算 / 记账 / 出具凭证，纯函数，不联网 |
| `carbonhub/adapters/` | 各数据源适配器 + 注册表 |
| `carbonhub/cli.py` | 端到端命令行 |
| `app.py` | Streamlit 演示界面 |
| `tools/` | 辅助脚本（PDF 取文字、界面截图） |
| `tests/` | 验收用例 |

## 适配器现状

| 适配器 | 状态 | 说明 |
|---|---|---|
| `manual` | connected | 界面手输，置信度 1.0 |
| `alipay_bill` | connected | 账单 CSV，列名映射表在 `alipay.py` 顶部 |
| `mock` | planned | 演示用假数据，产出带 `mock` 标记，别计入真实账本 |
| `utility_bill` / `fuel_receipt` | planned | 电费单 / 加油小票的拍照识别，等真实素材 |
| `wechat_bill` | planned | 换一份列名映射即可 |
| `ant_forest` / `bank_carbon` / `city_carbon` | planned | 无开放接口，等开放 |

## 两个设计决定（比对着文档读）

1. **适配器输出带 `warnings`**。认不出的行不静默丢弃，而是记下来交给界面显示。
   例：账单里那行"居民电费-9月"只有金额，账单给不了用电量，适配器会提示改用拍照识别。
2. **能拿到实物量才用实物量**。打车只有金额时按均价反推里程，`confidence` 压到 0.5，
   并在 `raw` 里写明是推算的。
