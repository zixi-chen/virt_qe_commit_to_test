# Virt QE Expert Knowledge Base

## Rule 1: CPU Model Migration Compatibility
- **Path Pattern**: `target/i386/cpu.c`
- **Code Change Pattern**:
  - 涉及 `builtin_x86_defs` 数组修改
  - 涉及 `FEAT_` 开头的 flag 增加（特别是默认开启的 flag）
- **Risk**: 导致 OpenShift 等上层产品在 src 和 dst QEMU 版本不一致时，迁移因 CPU Flag 不识别而报错。
- **Action**: 必须标注“迁移兼容性风险”，并建议增加跨版本迁移回归测试。

## Rule 2: Non-Regression of Conditional Logic
- **Pattern**: `if (condition) { return -ERROR; }`
- **Logic**: 当代码新增了针对特定场景的拒绝/限制逻辑时。
- **QE Action**: 
  1. 必须安排一个匹配 `condition` 的负向测试（确保被拒绝）。
  2. 必须安排一个**不匹配** `condition` 的正向测试（确保原有功能不被误杀）。