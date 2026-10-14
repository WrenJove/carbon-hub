"""减排规划：从"列清单"改成"按你的账本做约束优化"。

三层，缺一层就退化成碳普惠 App 的通用话术：

  1. 画像    先看你这份账的形状——哪一项占比异常、量级落在什么区间，
             不引用外部基准就说不清"异常"，所以区间是写死的经验值并标注出来。
  2. 候选    每条动作的减排量都由你自己的数据算出来，不是固定百分比；
             同时给出一次性投入、每年省/花多少钱。
  3. 规划    在"最多改 N 项、预算 ≤ B 元"的约束下，用 0/1 背包选减排量最大的
             组合。这就是动态规划：约束下的最优，不是把清单全塞给你。

所有参数都是量级估计，每条都写明假设。纯函数，不联网，可离线测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .models import ActivityRecord, CarbonLedger

# 人话换算用的常数
KG_PER_CAR_KM = 0.192          # 网约车每公里
KG_PER_TREE_YEAR = 12.0        # 一棵成年树一年固碳量（量级值）

# 经验区间，用来判断"你家这一项是否异常"。单位见 note。
REFERENCE_BANDS: dict[str, tuple[float, float, str]] = {
    "electricity": (100.0, 400.0, "度/月·户"),
    "fuel": (10.0, 100.0, "升/月"),
    "transport": (0.0, 300.0, "公里/月"),
    "diet": (0.0, 60.0, "餐/月"),
    "shopping": (0.0, 3000.0, "元/月"),
}

@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    action: str
    category: str
    reduction_kg: float
    setup_cost_yuan: float
    annual_delta_yuan: float  # 负数 = 每年反而省钱
    kind: str  # 设备 = 一次性动作；行为 = 需要持续改变日常习惯
    assumption: str
    rationale: str  # 为什么给你推荐这条（引用你的数据）

    @property
    def marginal_cost(self) -> float:
        """边际减排成本：每减一公斤碳，净花多少钱（负数=还省钱）。"""

        net = self.setup_cost_yuan + self.annual_delta_yuan
        return net / self.reduction_kg if self.reduction_kg > 0 else 0.0

@dataclass(frozen=True)
class ProfileNote:
    category: str
    share_percent: float
    monthly: float
    band: str
    verdict: str  # 偏高 / 正常 / 偏低

@dataclass
class Plan:
    profile: tuple[ProfileNote, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    chosen: tuple[Candidate, ...] = ()
    total_reduction_kg: float = 0.0
    total_setup_cost: float = 0.0
    total_annual_delta: float = 0.0
    ceiling_kg: float = 0.0      # 所有候选的总和（理论上限）
    cap_ceiling_kg: float = 0.0  # 只受类别上限时的最优
    budget_yuan: float | None = None
    max_actions: int | None = None
    category_cap_ratio: float = 0.4
    notes: list[str] = field(default_factory=list)

# ------------------------------------------------------------------ 画像

def _months(activities: Sequence[ActivityRecord], category: str | None = None) -> float:
    """账期覆盖的月数。

    必须按类别各算各的：十月份买了 28 升油，不能拿电费的"四个月"去摊，
    否则月均会被稀释，画像判断跟着错。
    """

    stamps = {
        str(a.occurred_at)[:7]
        for a in activities
        if a.occurred_at and (category is None or a.category == category)
    }
    return max(1.0, float(len(stamps)))

def build_profile(ledger: CarbonLedger, activities: Sequence[ActivityRecord]) -> tuple[ProfileNote, ...]:
    total = ledger.totals.get("total", 0.0) or 1.0

    quantities: dict[str, float] = {}
    for activity in activities:
        quantities[activity.category] = quantities.get(activity.category, 0.0) + activity.quantity

    notes: list[ProfileNote] = []
    for category, kg in sorted(ledger.by_category.items(), key=lambda kv: -kv[1]):
        band = REFERENCE_BANDS.get(category)
        if band is None:
            continue
        low, high, unit = band
        monthly = quantities.get(category, 0.0) / _months(activities, category)
        if monthly > high:
            verdict = "偏高"
        elif monthly < low:
            verdict = "偏低"
        else:
            verdict = "正常"
        notes.append(ProfileNote(
            category=category,
            share_percent=round(kg / total * 100, 1),
            monthly=round(monthly, 1),
            band=f"{low:g}~{high:g} {unit}（经验区间）",
            verdict=verdict,
        ))
    return tuple(notes)

# ------------------------------------------------------------------ 候选

def _candidates(ledger: CarbonLedger, activities: Sequence[ActivityRecord], profile) -> list[Candidate]:
    totals = dict(ledger.by_category)

    def quantity_of(category: str) -> float:
        return sum(a.quantity for a in activities if a.category == category)

    def year_scale_of(category: str) -> float:
        """把账期内的量放大成年度量。账期不足一年时这个系数会大于 1。"""

        return 12.0 / _months(activities, category)

    def annual_kg(category: str) -> float:
        """某类别的年度排放。所有建议的减排量都统一用年化口径，避免
        "减排量按月、省的钱按年"这种对不上的低级错误。"""

        return totals.get(category, 0.0) * year_scale_of(category)

    def electricity_price(activity_list) -> float:
        return 0.53  # 元/度，取电网因子同量级的口径，用于估算省多少钱

    out: list[Candidate] = []
    electricity_kg = totals.get("electricity", 0.0)
    if electricity_kg > 0:
        factor = electricity_kg / max(quantity_of("electricity"), 1e-9)  # kg/kWh
        kwh_year = quantity_of("electricity") * year_scale_of("electricity")
        price = 0.53

        # 待机功耗：这是最容易被忽略的一块，而且几乎零成本
        standby_kwh = min(60.0, max(15.0, kwh_year * 0.05))
        out.append(Candidate(
            id="standby",
            title="夜间切断待机功耗",
            action="给电视、机顶盒、路由器、显示器、充电器配一个带开关的插排，睡前按一下",
            category="electricity",
            reduction_kg=standby_kwh * factor,
            setup_cost_yuan=25.0,
            annual_delta_yuan=-standby_kwh * price,
            kind="设备",
            assumption=f"机顶盒+路由器+显示器待机约 5~15 W，按每天 12 小时计，年耗约 {standby_kwh:.0f} 度",
            rationale="待机功耗不产生使用价值，是家庭里性价比最高的一块",
        ))

        # 照明换 LED
        led_kwh = min(120.0, max(30.0, kwh_year * 0.08))
        out.append(Candidate(
            id="led",
            title="照明整体换 LED",
            action="把还亮着的白炽灯、卤素灯一次性换成 LED 灯泡",
            category="electricity",
            reduction_kg=led_kwh * factor,
            setup_cost_yuan=60.0,
            annual_delta_yuan=-led_kwh * price,
            kind="设备",
            assumption=f"同亮度下 LED 比白炽灯省约 75%，按你家照明年耗 {led_kwh:.0f} 度估算",
            rationale="一次投入、每年回本，生命周期内是负成本减排",
        ))

        # 空调调高一度
        ac_kwh = min(200.0, max(40.0, kwh_year * 0.12))
        out.append(Candidate(
            id="ac-temp",
            title="空调设定温度调高 1 度",
            action="夏天设在 26 度而不是 25 度，配合风扇体感接近",
            category="electricity",
            reduction_kg=ac_kwh * 0.08 * factor,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-ac_kwh * 0.08 * price,
            kind="设备",
            assumption="制冷电耗约占夏季用电三成，每调高 1 度约省 7~8% 制冷电耗",
            rationale="零成本、立刻生效，是夏季最直接的一个杠杆",
        ))

        # 大件换新：这类动作减排量大，但一次性投入也大，预算约束在这里才咬得住
        out.append(Candidate(
            id="ac-replace",
            title="空调换一级能效机型",
            action="把超期服役的定频空调换成一机能效变频机；换新前先确认旧机确实在拖后腿",
            category="electricity",
            reduction_kg=kwh_year * 0.15 * factor,
            setup_cost_yuan=2500.0,
            annual_delta_yuan=-kwh_year * 0.15 * price,
            kind="设备",
            assumption="老旧定频机比一级能效变频机多耗约 15% 全年电，按你账期内的用量年化后估算",
            rationale="单次投入能拿到最大一块减排，但要想清楚旧机还能不能撑",
        ))

        out.append(Candidate(
            id="water-heater-heatpump",
            title="电热水器换成空气能热水器",
            action="储水式电热水器用到年限后，换代时直接选空气能机型",
            category="electricity",
            reduction_kg=kwh_year * 0.10 * factor,
            setup_cost_yuan=3000.0,
            annual_delta_yuan=-kwh_year * 0.10 * price,
            kind="设备",
            assumption="储水式电热水器常年保温损耗大，换空气能大约能省下全屋用电一成",
            rationale="热水器是家里最容易被忽略的常耗电设备，而且换机窗口明确",
        ))
        # 冰箱散热
        fridge_kwh = min(80.0, max(20.0, kwh_year * 0.06))
        out.append(Candidate(
            id="fridge-gap",
            title="给冰箱留出散热空间",
            action="冰箱背面和两侧留 10 厘米空隙，别贴着墙；远离灶台和阳光直射",
            category="electricity",
            reduction_kg=fridge_kwh * 0.07 * factor,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-fridge_kwh * 0.07 * price,
            kind="设备",
            assumption="冰箱常年运行且散热受阻会多耗 5~10% 电，按年耗估算",
            rationale="纯靠摆位就能省，属于典型的低成本高回报",
        ))

        # 洗衣机改冷水
        wash_kwh = min(150.0, max(30.0, kwh_year * 0.10))
        out.append(Candidate(
            id="washing-cold",
            title="洗衣机改用冷水洗涤",
            action="把默认的加热洗改成冷水洗，除非是有油污的衣物",
            category="electricity",
            reduction_kg=wash_kwh * 0.80 * factor,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-wash_kwh * 0.80 * price,
            kind="设备",
            assumption="洗衣机耗电里约八成用于加热水，只改水温不改习惯",
            rationale="不改生活习惯，只改一个按钮，减排幅度却很大",
        ))

    if totals.get("fuel", 0.0) > 0:
        liters = quantity_of("fuel")
        fuel_year_scale = year_scale_of("fuel")
        out.append(Candidate(
            id="tire-pressure",
            title="把胎压打到标准值",
            action="每月检查一次胎压，按车门框标注值补气；顺手清掉后备箱里的杂物",
            category="fuel",
            reduction_kg=annual_kg("fuel") * 0.02,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-liters * fuel_year_scale * 8.0 * 0.02,  # 按 8 元/升估
            kind="设备",
            assumption="胎压不足与额外载重合计约多耗 2% 油",
            rationale="不动驾驶习惯，纯机械保养就能拿到的减排量",
        ))
        out.append(Candidate(
            id="short-trip",
            title="3 公里内不开车",
            action="买菜、取快递这种短途改成走路或骑车",
            category="fuel",
            reduction_kg=annual_kg("fuel") * 0.08,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-liters * fuel_year_scale * 8.0 * 0.08,
            kind="行为",
            assumption="短途冷启动油耗显著高于匀速行驶，假定这部分里程占总量 8%",
            rationale="短途行程是油耗和排放最不划算的一类，替换成本却最低",
        ))

    if totals.get("transport", 0.0) > 0:
        km = quantity_of("transport")
        transport_year_scale = year_scale_of("transport")
        out.append(Candidate(
            id="carpool",
            title="网约车改拼车或顺风车",
            action="非急事时选拼车；固定通勤路线可以找同路的同事拼一下",
            category="transport",
            reduction_kg=annual_kg("transport") * 0.30,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-km * transport_year_scale * 2.5 * 0.25,  # 按 2.5 元/公里估
            kind="行为",
            assumption="拼车把同一次行程的排放摊给多人，按你自己的里程折算可减约三成",
            rationale="不用改路线，改的是乘车方式",
        ))
        out.append(Candidate(
            id="telecommute",
            title="每周一天远程替代通勤",
            action="把能线上开的会挪到线上，一周留一天不去现场",
            category="transport",
            reduction_kg=annual_kg("transport") * 0.20,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-km * transport_year_scale * 2.5 * 0.20,
            kind="行为",
            assumption="按每周减少一天通勤里程估算，约占月度里程两成",
            rationale="减的是整段行程，不只是换交通方式",
        ))

    if totals.get("diet", 0.0) > 0:
        out.append(Candidate(
            id="dine-in",
            title="外卖改到店吃",
            action="把一部分外卖改成走去店里吃，顺带省掉打包和配送",
            category="diet",
            reduction_kg=annual_kg("diet") * 0.25,
            setup_cost_yuan=0.0,
            annual_delta_yuan=0.0,
            kind="行为",
            assumption="到店省掉一次性餐具、打包和末端配送，按单餐排放减约两成半估算",
            rationale="这一项减的不只是餐食本身，还有包装和配送那一段",
        ))

    if totals.get("shopping", 0.0) > 0:
        out.append(Candidate(
            id="repair-first",
            title="先修再换",
            action="小家电、鞋子、背包先找维修点问价，能修就不换新",
            category="shopping",
            reduction_kg=annual_kg("shopping") * 0.15,
            setup_cost_yuan=0.0,
            annual_delta_yuan=-annual_kg("shopping") * 0.15 * 0.3,  # 粗略按省下三成支出
            kind="行为",
            assumption="维修替代换新大约能省下这类支出的一成半，按支出强度折算",
            rationale="买东西的排放主要在制造环节，延长使用寿命是最直接的办法",
        ))

    if totals.get("water", 0.0) > 0:
        out.append(Candidate(
            id="water-device",
            title="装节水起泡器",
            action="给厨房和卫生间龙头装起泡器，花洒换增压节水款",
            category="water",
            reduction_kg=annual_kg("water") * 0.12,
            setup_cost_yuan=40.0,
            annual_delta_yuan=-annual_kg("water") * 0.12 * 0.3,
            kind="设备",
            assumption="起泡器在体感不变的前提下省水约一成",
            rationale="省水同时省掉加热它的那部分能耗",
        ))

    for item in out:
        if item.reduction_kg <= 0:
            continue
    return [c for c in out if c.reduction_kg > 0]

# ------------------------------------------------------------------ 规划

def _best_subset(candidates, caps, budget, max_actions):
    """在约束下找减排量最大的组合。

    约束有三个：一次性投入上限、最多改几项、每个类别的减排不超过该类年度排放的
    一定比例（不可能把一个类别的排放全砍光）。

    算法说明：候选只有十来条，直接枚举所有子集就是精确解，比套动态规划更简单也更好
    验证。等动作库长到几百条时再换成背包 DP，现在写 DP 是拿锤子找钉子。
    """

    n = len(candidates)
    if n == 0:
        return []
    limit = n if max_actions is None else min(n, max_actions)
    best_value, best_mask = -1.0, 0

    for mask in range(1 << n):
        if bin(mask).count("1") > limit:
            continue
        cost = 0.0
        value = 0.0
        used: dict[str, float] = {}
        feasible = True

        for index in range(n):
            if not (mask >> index) & 1:
                continue
            item = candidates[index]
            cost += item.setup_cost_yuan
            if budget is not None and cost > budget + 1e-6:
                feasible = False
                break
            value += item.reduction_kg
            used[item.category] = used.get(item.category, 0.0) + item.reduction_kg

        if not feasible:
            continue
        for category, amount in used.items():
            if amount > caps.get(category, float("inf")) + 1e-6:
                feasible = False
                break
        if not feasible:
            continue

        if value > best_value:
            best_value, best_mask = value, mask

    return [candidates[i] for i in range(n) if (best_mask >> i) & 1]

@dataclass(frozen=True)
class Step:
    """预算台阶：在某笔钱之内能达到的最优解。"""

    cost_yuan: float
    reduction_kg: float
    ids: tuple[str, ...]


def frontier(
    ledger: CarbonLedger,
    activities: Sequence[ActivityRecord],
    max_actions: int | None = None,
    category_cap_ratio: float = 0.4,
    allowed_kinds: Sequence[str] = ("设备", "行为"),
) -> list[Step]:
    """把所有可行的 (投入, 减排) 组合压成台阶。

    钱不是连续起作用的，只在"够不够买某件东西"的几个价格点上起作用，
    中间大片区间是平的。与其让用户填一个连续数字，不如把台阶直接给他。
    """

    profile = build_profile(ledger, activities)
    candidates = [c for c in _candidates(ledger, activities, profile) if c.kind in allowed_kinds]
    n = len(candidates)
    if n == 0:
        return []

    caps: dict[str, float] = {}
    for category in {c.category for c in candidates}:
        annual = ledger.by_category.get(category, 0.0) * (12.0 / _months(activities, category))
        caps[category] = annual * category_cap_ratio

    limit = n if max_actions is None else min(n, max_actions)
    feasible: list[tuple[float, float, tuple[str, ...]]] = []

    for mask in range(1 << n):
        if bin(mask).count("1") > limit:
            continue
        cost = 0.0
        value = 0.0
        used: dict[str, float] = {}
        ids: list[str] = []
        for index in range(n):
            if not (mask >> index) & 1:
                continue
            item = candidates[index]
            cost += item.setup_cost_yuan
            value += item.reduction_kg
            used[item.category] = used.get(item.category, 0.0) + item.reduction_kg
            ids.append(item.id)
        if any(amount > caps.get(cat, float("inf")) + 1e-6 for cat, amount in used.items()):
            continue
        feasible.append((cost, value, tuple(sorted(ids))))

    # 只留帕累托最优：花的钱更多、减排却没更多，就没有意义
    feasible.sort(key=lambda item: (item[0], -item[1]))
    steps: list[Step] = []
    best = -1.0
    for cost, value, ids in feasible:
        if value > best + 1e-6:
            steps.append(Step(cost_yuan=round(cost, 1), reduction_kg=round(value, 1), ids=ids))
            best = value
    return steps

def plan(
    ledger: CarbonLedger,
    activities: Sequence[ActivityRecord],
    budget_yuan: float | None = None,
    max_actions: int | None = None,
    category_cap_ratio: float = 0.4,
    allowed_kinds: Sequence[str] = ("设备", "行为"),
) -> Plan:
    """给一份账本做减排规划。

    category_cap_ratio 是每个类别的减排上限比例：0.4 表示最多砍掉该类年度排放的四成。
    这个约束是真会咬人的——用电类的动作有四个，一起选就会超，必须取舍。
    """

    profile = build_profile(ledger, activities)
    candidates = [c for c in _candidates(ledger, activities, profile) if c.kind in allowed_kinds]
    candidates.sort(key=lambda c: -c.reduction_kg)

    # 每个类别的年度排放 × 上限比例 = 该类最多能减多少
    months_cache: dict[str, float] = {}
    caps: dict[str, float] = {}
    for category in {c.category for c in candidates}:
        months_cache[category] = _months(activities, category)
        annual = ledger.by_category.get(category, 0.0) * (12.0 / months_cache[category])
        caps[category] = annual * category_cap_ratio

    # 只受类别上限时的最优，用来对照"预算和项数到底卡掉了多少"
    cap_only = _best_subset(candidates, caps, None, None)
    cap_only_kg = sum(c.reduction_kg for c in cap_only)

    chosen = _best_subset(candidates, caps, budget_yuan, max_actions)
    chosen.sort(key=lambda c: -c.reduction_kg)
    total = sum(c.reduction_kg for c in chosen)

    notes: list[str] = []
    names = {"electricity": "用电", "fuel": "用油", "transport": "出行",
             "diet": "外卖", "shopping": "购物", "water": "用水", "gas": "燃气"}

    high = [p for p in profile if p.verdict == "偏高"]
    if high:
        notes.append("你这份账里 " + "、".join(names.get(p.category, p.category) for p in high)
                     + " 偏离常见区间，排序已按你的实际数据算过，不是通用清单。")

    thin = [cat for cat in {a.category for a in activities} if _months(activities, cat) < 3]
    if thin:
        notes.append("这些类别的单据不足三个月，年度数字是从现有单据外推的，只作量级参考："
                     + "、".join(names.get(c, c) for c in thin))

    capped = [cat for cat, cap in caps.items()
              if sum(c.reduction_kg for c in candidates if c.category == cat) > cap + 1e-6]
    if capped:
        notes.append("这些类别的可选动作加起来超过了上限比例 "
                     f"({category_cap_ratio:.0%})，已经从里面挑了性价比最高的："
                     + "、".join(names.get(c, c) for c in capped))

    # 逐条放宽，看看到底是哪条约束在咬人。只报告真正咬住的，
    # 把没起作用的约束也列出来属于糊弄人。
    probes = []
    if budget_yuan is not None:
        probes.append(("投入上限", f"投入上限 {budget_yuan:.0f} 元",
                       _best_subset(candidates, caps, None, max_actions)))
    if max_actions is not None:
        probes.append(("项数上限", f"最多改 {max_actions} 项",
                       _best_subset(candidates, caps, budget_yuan, None)))
    probes.append(("类别上限", f"单类别上限 {category_cap_ratio:.0%}",
                   _best_subset(candidates, {}, budget_yuan, max_actions)))

    binding = []
    for _key, label, relaxed in probes:
        gain = sum(c.reduction_kg for c in relaxed) - total
        if gain > 0.5:
            binding.append((label, gain))

    if binding:
        binding.sort(key=lambda item: -item[1])
        notes.append(
            "真正卡住你的是："
            + "；".join(f"{label}（放宽能再多减 {gain:.1f} kg）" for label, gain in binding)
        )
    elif capped:
        notes.append("当前组合已经吃满了可选动作，没有约束卡住。")

    # 告诉用户：他给的这个预算是不是落在平台区（多花的钱没有用）
    if budget_yuan is not None:
        affordable = [s for s in frontier(
            ledger, activities, max_actions, category_cap_ratio, allowed_kinds
        ) if s.cost_yuan <= budget_yuan + 1e-6]
        if affordable:
            best_step = max(affordable, key=lambda s: s.reduction_kg)
            if budget_yuan - best_step.cost_yuan >= 100 and abs(best_step.reduction_kg - total) < 0.5:
                notes.append(
                    f"你填的 {budget_yuan:.0f} 元落在平台区：它和 {best_step.cost_yuan:.0f} 元的效果完全一样，"
                    f"多出的 {budget_yuan - best_step.cost_yuan:.0f} 元不会带来任何额外减排。"
                    "钱只在能买下某件东西的那几个价格点上起作用。"
                )

    gap = cap_only_kg - total
    if gap > 0.5 and not binding:
        notes.append(f"比只受类别上限时少拿 {gap:.1f} kg。")

    return Plan(
        profile=profile,
        candidates=tuple(candidates),
        chosen=tuple(chosen),
        total_reduction_kg=round(total, 1),
        total_setup_cost=round(sum(c.setup_cost_yuan for c in chosen), 1),
        total_annual_delta=round(sum(c.annual_delta_yuan for c in chosen), 1),
        ceiling_kg=round(sum(c.reduction_kg for c in candidates), 1),
        cap_ceiling_kg=round(cap_only_kg, 1),
        budget_yuan=budget_yuan,
        max_actions=max_actions,
        category_cap_ratio=category_cap_ratio,
        notes=notes,
    )

KIND_RULE = (
    "「设备」= 一次性动作，买个东西或调个设置，做完不用管；"
    "「行为」= 需要持续改变日常习惯。分界线是「要不要一直记着这件事」，不是花钱多少。"
)


def humanize(kg: float) -> str:
    """把公斤数换成能感知的量。"""

    km = kg / KG_PER_CAR_KM
    tree_days = kg / KG_PER_TREE_YEAR * 365
    return f"约等于开车 {km:,.0f} 公里，或一棵树 {tree_days:,.0f} 天的固碳量"
