/**
 * 适配器模板：新增一个数据源时照着改。
 *
 * 铁律：
 *   1. 适配器只负责"读出来 + 归一化"，不负责算排放、不负责画界面。
 *   2. 只用用户自己导出 / 自己拍摄 / 自己手输的数据；不存账号密码，
 *      不代登录第三方平台，不抓登录态接口。
 *   3. 拿不准的一律留 raw 原文，交给人工复核，别猜。
 */
import type { ActivityRecord, AdapterCapability, CarbonAdapter, RawInput } from '../types';

const capability: AdapterCapability = {
  id: 'example_source',
  displayName: '示例来源',
  status: 'planned', // 实现完改成 connected
  ingest: ['file-csv'],
  categories: ['electricity'],
  note: '一句话说明这个来源能拿到什么、拿不到什么',
};

export const adapter: CarbonAdapter = {
  capability,

  async ingest(input: RawInput): Promise<ActivityRecord[]> {
    void input;
    // 1) 解析原始素材（CSV 行 / 图片 OCR 结果 / 手输对象）
    // 2) 逐行映射到 ActivityCategory + 归一化单位
    // 3) 打 confidence（OCR 低分要标出来）
    // 4) 返回 ActivityRecord[]
    return [];
  },
};
