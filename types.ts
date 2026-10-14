/**
 * carbon-hub 核心契约 v0.1
 *
 * 目的：把任意来源的碳相关活动数据，归一化成同一份碳账本；把结果折成一份
 *       任何平台都能读的"碳数据凭证"。
 *
 * 本文件只定义契约，不含实现。实现分五层：
 *   L1 采集 Adapter.ingest   RawInput      -> ActivityRecord[]
 *   L2 归一（在 Adapter 内完成，保证单位/分类统一）
 *   L3 核算 Calculator       ActivityRecord x EmissionFactor -> EmissionRecord
 *   L4 记账 Ledger           -> CarbonLedger
 *   L5 出具 Passport         -> CarbonPassport
 *
 * 新增一个数据源 = 新增一个 CarbonAdapter 实现，内核不动。
 */

export type Scope = 1 | 2 | 3;

/** 事实分类，决定匹配哪一类排放因子 */
export type ActivityCategory =
  | 'electricity' // 用电
  | 'gas' // 天然气 / 燃气
  | 'fuel' // 汽油 / 柴油
  | 'transport' // 地面出行（公交 / 地铁 / 打车 / 骑行 / 步行）
  | 'flight' // 航空
  | 'delivery' // 快递 / 外卖配送
  | 'diet' // 饮食
  | 'shopping' // 购物消费
  | 'water' // 用水
  | 'waste' // 垃圾 / 回收
  | 'other';

/** 归一化后的计量单位 */
export type Unit =
  | 'kWh'
  | 'm3'
  | 'L'
  | 'km'
  | 'person-km'
  | 'kg'
  | 'CNY'
  | 'count'
  | 'meal';

/** 数据怎么进来的 */
export type IngestKind = 'api' | 'file-csv' | 'file-image' | 'manual';

/** L1 输入：一次可读的原始素材 */
export interface RawInput {
  sourceId: string;
  kind: 'csv' | 'image' | 'json' | 'text' | 'manual';
  /** 文件路径 / 图片路径 / 文本 / 手输对象，由适配器自己解释 */
  payload: unknown;
  capturedAt: string; // ISO8601
}

/** L2 输出：系统的原子单位，一条 = 一次可核算的活动 */
export interface ActivityRecord {
  id: string;
  sourceId: string; // 来自哪个适配器
  occurredAt: string; // ISO8601
  category: ActivityCategory;
  quantity: number; // 按 unit 计量
  unit: Unit;
  /** 原始值，保留以便追溯与人工复核 */
  raw?: { quantity?: number; unit?: string; description?: string; row?: unknown };
  /** 0~1：OCR / 规则推断的置信度；人工录入为 1 */
  confidence: number;
}

/** 排放因子：来自独立因子库，可整体替换升级 */
export interface EmissionFactor {
  id: string;
  category: ActivityCategory;
  unit: Unit;
  /** kgCO2e / unit */
  factor: number;
  scope: Scope;
  region?: string; // 'CN' | 'CN-JS' ...
  year: number;
  /** 依据来源，例如 '生态环境部 全国电网平均排放因子 2024' */
  source: string;
}

/** L3 输出：一条活动对应的排放 */
export interface EmissionRecord {
  activityId: string;
  kgCO2e: number;
  scope: Scope;
  factorId: string;
}

/** L4：一段时期内的碳账本 */
export interface CarbonLedger {
  subject: string; // 用户 / 组织标识
  period: { from: string; to: string };
  totals: { scope1: number; scope2: number; scope3: number; totalKgCO2e: number };
  byCategory: Partial<Record<ActivityCategory, number>>;
  emissions: EmissionRecord[];
  methodology: { standard: string; factorsVersion: string };
}

/** L5：对外交换的碳数据凭证（互通层，对应"统一数据格式 / 接口规范"） */
export interface CarbonPassport {
  specVersion: string; // 'carbon-passport/0.2'
  subject: string;
  period: { from: string; to: string };
  totalKgCO2e: number;
  /** 相对基准的减排量，可为空 */
  reductionKgCO2e?: number;
  entries: Array<{
    activityId: string;
    sourceId: string;
    occurredAt: string;
    category: ActivityCategory;
    kgCO2e: number;
    scope: Scope;
  }>;
  /** 对原始记录做哈希，解决"数据确权 / 可信"问题 */
  evidenceHashes: string[];
  issuedAt: string;
}

/** 适配器能力声明：驱动界面上的"已接入 / 可接入 / 待接入" */
export interface AdapterCapability {
  id: string;
  displayName: string;
  /** connected=已实现可用；available=接口已留、等对接；planned=只占位 */
  status: 'connected' | 'available' | 'planned';
  ingest: IngestKind[];
  categories: ActivityCategory[];
  note?: string;
}

/** 适配器输出：记录 + 认不出的行（不静默丢弃） */
export interface IngestResult {
  records: ActivityRecord[];
  warnings: string[];
}

/** 每个数据源实现这一个接口 */
export interface CarbonAdapter {
  capability: AdapterCapability;
  /** 素材 -> 统一活动记录 */
  ingest(input: RawInput): Promise<IngestResult>;
}
