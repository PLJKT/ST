# data/（公开镜像）

本目录在公开仓库里只有 `universe.csv`（股票池，非敏感）。

原始数据全部在私有仓库 `ST-archive/data/`：单据 PDF、转文本、归一化 JSON、
`st.db`、表快照 `snapshot/*.csv`、`manifest.json`（每个文件的 sha256）。

为什么拆开：PDF 里带姓名、地址、银行/券商账号，无法可靠打码，只能整包私有。
公开站点的数字由 `tools/export_static.py` 经 `tools/redact.py` 脱敏后生成，
每次发布前跑 `python tools/redact_audit.py`，必须 PASS。
