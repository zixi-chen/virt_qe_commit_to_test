# Virt QE Agent for Backport Analysis

面向 Virt QE 的补丁分析 Agent。输入 patch（单文件或目录），结合规则与经验库，自动生成测试计划报告。

## 项目结构

```text
commit_to_test/
├── qe_agent/                   # Agent package
│   ├── cli.py                  # CLI 参数与入口
│   ├── core.py                 # 核心分析逻辑
│   ├── tp_qemu_repo.py         # tp-qemu git clone / pull
│   ├── config.py               # 规则与常量
│   ├── models.py               # 数据模型
│   └── __main__.py             # python -m qe_agent
├── .agent_context/             # 映射索引与经验库
├── virt_qe_agent.py            # 兼容旧入口
├── requirements.txt
└── README.md
```

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中配置：

```bash
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash

# tp-qemu：默认每次运行会自动 clone（若目录不存在）或 git pull --ff-only（需要本机 git）
TP_QEMU_GIT_URL=https://github.com/autotest/tp-qemu.git
TP_QEMU_BRANCH=
# 离线或不想每次拉取时：TP_QEMU_AUTO_SYNC=0
```

## tp-qemu 测试代码（克隆或更新）

默认 `--tests-root` 为当前工作目录下的 `tp-qemu`。每次运行 **`python3 -m qe_agent`** 或 **`python3 generate_index.py`** 时会自动：目录不存在则 **`git clone`**；已是 git 仓库则 **`git pull --ff-only`**。仓库地址与可选分支由 `TP_QEMU_GIT_URL`、`TP_QEMU_BRANCH` 指定（见 `.env.example`）。若需完全跳过网络同步（例如离线），在 `.env` 中设置 **`TP_QEMU_AUTO_SYNC=0`**。

重新生成映射索引：

```bash
python3 generate_index.py
```

## 运行方式

推荐新入口（包方式）：

```bash
python3 -m qe_agent --input ./patches/sample.patch --mode single --output test_plan_report.md
```

兼容旧入口（仍可用）：

```bash
python3 virt_qe_agent.py --input ./patches/sample.patch --mode single --output test_plan_report.md
```

按子系统聚类：

```bash
python3 -m qe_agent --input ./backport_patches --mode cluster --output test_plan_report.md
```

详细日志：

```bash
python3 -m qe_agent --input ./patches/sample.patch --mode single --verbose
```

同时导出 Excel 友好的表格（CSV）：

```bash
python3 -m qe_agent \
  --input ./patches/sample.patch \
  --mode single \
  --output test_plan_report.md \
  --excel-output test_plan_report.csv
```

## `.patch` 输入配置

支持两种输入形式（通过 `--input` 指定）：

- 单个 patch 文件：`--input ./patches/sample.patch`
- patch 目录（批量分析）：`--input ./patches`

建议约定：

- 使用 `.patch` 后缀，放在独立目录（例如 `./patches/`）便于管理。
- 路径中若有空格，使用双引号包裹，例如 `--input "./patches/6701 fix.patch"`。
- 在 `single` 模式下建议传入单文件；在 `cluster` 模式下建议传入目录。

## 主要参数

- `--input`: patch 文件路径或 patch 目录
- `--mapping`: 测试映射索引（默认 `.agent_context/mapping_index.json`）
- `--experience`: 经验库（默认 `.agent_context/experience_base.md`）
- `--cursorrules`: 核心规则（默认 `.cursorrules`）
- `--tests-root`: 测试代码根目录（默认 `tp-qemu`，相对路径相对当前工作目录解析）
- `--mode`: `single` / `cluster`
- `--output`: 输出报告路径（默认 `test_plan_report.md`）
- `--excel-output`: 可选，输出 CSV 表格（Excel 可直接打开）

## 输出

默认输出 Markdown 报告，内容包括：

- Commit/Cluster 级分析结论
- 回归测试集合与筛选说明
- 新用例设计建议
- 基于测试源码上下文的覆盖判断
