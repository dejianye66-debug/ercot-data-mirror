# ERCOT RTM data mirror for the M3 workbook

This repository checks ERCOT public report type `13061` once per day from a standard GitHub-hosted runner. When ERCOT publishes a new annual archive, the workflow validates the ZIP and replaces two stable assets in the `ercot-latest` release:

- `ercot-latest.zip`
- `latest.json`

The Windows M3 updater reads these assets only when direct access to ERCOT is unavailable. The mirrored file remains the original ERCOT ZIP; the workflow records its size and SHA-256 checksum before publishing it.

## Manual first run

Open **Actions → Mirror ERCOT RTM archive → Run workflow**. A successful run creates the `ercot-latest` release. After that, the scheduled workflow checks once per day.

## Official source

- Product page: https://www.ercot.com/mp/data-products/data-product-details?id=NP6-785-ER
- Report type: `13061`

本仓库只中转 ERCOT 的公开文件，不包含工作簿、账号或任何私人数据。公开仓库使用标准 GitHub-hosted runner 时，GitHub Actions 免费。
