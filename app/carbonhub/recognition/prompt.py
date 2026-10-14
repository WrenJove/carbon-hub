"""识别配方：票种模板 + 提示词。

全项目最需要反复调的就是这一段。改这里不必动其它任何代码。
调法：拿真实单据跑一遍，看哪一类字段老出错，就补哪一类的说明。

注意一个中国单据的特点：同一张单上"金额"有三种口径——不含税金额、含税价税合计、
以及再叠加代收基金后的合计。所以交叉验算不能用等号，只能看比值落在合理带内。
"""

from __future__ import annotations

BILL_PROMPT = """你在帮一个碳核算程序读能源单据。请从图片中抽取字段，只输出 JSON。

第一步判断票种，第二步抽对应字段。
抽不到的字段一律填 null。绝对不要推测、不要补全、不要根据上下文猜数字。

票种一 electricity（电费单、电费发票、电费账单截图）：
  accountNo   户号
  periodStart 计费周期开始，格式 YYYY-MM-DD
  periodEnd   计费周期结束，格式 YYYY-MM-DD
  billDate    单据日期。抄表日期、开票日期、通知时间都算，格式 YYYY-MM-DD。
              像"电费通知单""收费收据"这类没有计费周期的单子，这个字段尤其重要
  kwh         本期用电量，单位千瓦时（也就是"度"）
  amountCny   本期电费金额。单上有多个金额时，填你实际支付的那个合计金额
  unitPrice   电价，元/千瓦时（按单据上写的填，可能是不含税单价）
  peakKwh     峰段电量（有才填）
  valleyKwh   谷段电量（有才填）

票种二 fuel（加油小票、加油发票）：
  date        加油日期，格式 YYYY-MM-DD
  fuelGrade   油品，例如 95号汽油
  liters      升数
  unitPrice   单价，元/升（按单据上写的填，可能是不含税单价）
  amountCny   金额。单上既有不含税金额又有价税合计时，填价税合计

票种三 unknown：认不出票种时用这个，其它字段全部 null。

输出格式严格照抄下面的结构，不要加解释文字：
{"docType":"electricity","electricity":{"accountNo":null,"periodStart":null,"periodEnd":null,"billDate":null,"kwh":null,"amountCny":null,"unitPrice":null,"peakKwh":null,"valleyKwh":null},"fuel":{"date":null,"fuelGrade":null,"liters":null,"unitPrice":null,"amountCny":null},"confidence":0.0,"notes":""}

confidence 是你对整张单子抽取结果的把握，0 到 1 之间的小数。
notes 里可以写一句"这张单子上有手写改动"之类影响判读的情况。
"""

# 交叉验算：数量×单价的乘积 与 单据金额 的比值合理带。
# 带子放得比较宽，是因为含税、代收基金、阶梯加价都会让两者不相等；
# 超出这个带子才说明大概率读错了。
ARITHMETIC_BAND = (0.90, 1.30)

# 量级合理性区间，用来抓"小数点读错一个数量级"这类错
ELECTRICITY_KWH_RANGE = (0.1, 5000.0)
FUEL_LITERS_RANGE = (0.5, 200.0)
