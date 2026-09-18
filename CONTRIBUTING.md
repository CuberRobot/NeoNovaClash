# 开发约定

这份约定是为了让项目**随时可以回到稳定版本**，请务必遵守。

## 提交前自检

```bash
./venv/bin/python -m pytest -q      # 必须全绿
./venv/bin/ruff check .             # 必须无告警
node --check frontend/js/app.js     # 改了前端 JS 时执行
```

## 提交规范

提交信息用「类型(范围): 说明」的形式，说明写清**做了什么、为什么**：

```text
feat(engine): 新增斩杀标签的伤害修正
fix(web): 修复对手离开后房间未回收的问题
docs(rules): 补充中毒层数上限的裁定说明
test(rules): 覆盖自爆步兵必须放首位的校验
chore(deps): 升级 fastapi 到 0.141
```

常用类型：`feat` / `fix` / `docs` / `test` / `refactor` / `perf` / `chore`。

## 分支与合并

- `main`：始终可运行，只接受通过测试的合并；
- `feat/<主题>`、`fix/<主题>`：日常开发；
- 每个里程碑打 tag（例如 `v0.1.0`），打 tag 前必须完成
  [docs/开发流程与版本管理.md](docs/开发流程与版本管理.md) 里的发布检查清单。

## 代码约定

- 注释、文档、面向玩家的文案统一使用中文；
- 平衡数值只写在 `backend/core/constants.py`，角色数据只写在 `backend/core/characters.py`；
- 新增标签：在 `tags.py` 注册 `TagSpec` 与 `TagRuntime` 钩子，必要时才改 `engine.py`；
- 新增角色：在 `characters.py` 追加一条数据并补一条测试；
- 面向玩家的报错必须「说人话」：告诉玩家哪里不对、应该怎么改。
