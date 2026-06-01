# Contributing to solar-brightness

欢迎贡献！

## 快速开始

```bash
git clone https://github.com/zklovekfc/solar-brightness.git
cd solar-brightness
pip3 install pyyaml
```

## 提交流程

1. Fork 本项目
2. 创建分支: `feat/your-feature` 或 `fix/your-bug`
3. 编写代码，保持与现有代码风格一致
4. 提交前测试: `python3 solar-brightness.py --once`
5. 提交 PR 到 `main` 分支

## 代码风格

- Python 3.9+ 兼容
- 函数用小写下划线命名
- 注释用中文解释核心逻辑
- 日志用英文 + emoji 前缀

## 添加你的城市

如果 `solar-brightness.py` 的 `CITY_COORDS_CN` 字典中没有你的城市，请按格式提交：

```python
"城市名": (纬度, 经度),
```

## 报告 Bug

请在 Issue 中附上：
- macOS 版本和芯片型号
- 显示器型号和连接方式
- `--once` 运行的完整日志
- `m1ddc display list detailed` 输出
