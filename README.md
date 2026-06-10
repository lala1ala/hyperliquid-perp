# Hyperliquid Perp 地址交易监控机器人

这是一个轻量级、完全免费且免服务器开机的 Hyperliquid 地址监控警报工具。通过 **GitHub Actions**，它能够每隔 30 分钟自动扫描您指定的钱包地址，并将新发生的开仓/平仓交易汇总发送至您的 Telegram 机器人。

## 📁 目录结构

* `hl_tracker_config.json` - 监控的钱包地址配置文件（您需要在此处配置监控地址）
* `hyperliquid_polling_tracker.py` - 核心逻辑 Python 脚本
* `.github/workflows/run_tracker.yml` - GitHub Actions 自动化工作流配置
* `test_tg_conn.py` - Telegram 连接一键测试脚本
* `.env` - 本地测试使用的环境变量文件（**请勿提交此文件到 GitHub 仓库**）

---

## 🛠️ GitHub 部署与配置步骤

由于您已经创建了私有仓库 `https://github.com/lala1ala/hyperliquid-perp`，请按照以下步骤配置：

### 第一步：将代码推送至您的 GitHub 仓库

如果您已经熟悉 Git 操作，可直接将当前文件夹中的所有文件提交并推送到您的 GitHub 仓库。如果没有，可以打开命令行，进入该文件夹并执行以下命令：

```bash
# 初始化 Git 并关联您的仓库
git init
git remote add origin https://github.com/lala1ala/hyperliquid-perp.git
git branch -M main

# 忽略本地 .env 文件防止泄露凭证
echo ".env" >> .gitignore

# 提交并推送到 GitHub
git add .
git commit -m "feat: init hyperliquid tracker bot"
git push -u origin main
```

---

### 第二步：配置 GitHub Repository Secrets (加密环境变量)

为了保护您的 Telegram Token 不泄露，我们需要在 GitHub 后台配置 Secrets：

1. 打开您的 GitHub 仓库页面：[lala1ala/hyperliquid-perp](https://github.com/lala1ala/hyperliquid-perp)
2. 点击页面右上角的 **Settings** (设置)。
3. 在左侧菜单栏中，依次点击 **Secrets and variables** -> **Actions**。
4. 点击右上角的 **New repository secret** (新建仓库密钥)。
5. 分别添加以下两个密钥：
   * **密钥名称**：`TELEGRAM_BOT_TOKEN`
     * **密钥内容**：`8630179711:AAFiKjLSKWibovjVzZtMv4n55gMH0tAoX2o`
   * **密钥名称**：`TELEGRAM_CHAT_ID`
     * **密钥内容**：`991021964`

---

### 第三步：开启 GitHub Actions 读写权限 (至关重要)

因为机器人需要更新并保存 `seen_trades.json` 文件（防止下次运行重复推送旧交易），所以必须授予 GitHub Actions 写入权限：

1. 同样在仓库的 **Settings** (设置) 页面中。
2. 在左侧菜单栏，点击 **Actions** 下方的 **General**。
3. 滚动到页面最底部，找到 **Workflow permissions** (工作流权限)。
4. 将默认的 "Read repository contents and packages permissions" 改为 **"Read and write permissions"**。
5. 点击 **Save** (保存)。

---

## 🚀 首次测试与运行

1. 在 Telegram 中搜索 `@hyperperpbbot`，并点击底部的 **【Start】（开始）** 按钮激活机器人（您之前报错 `chat not found` 就是因为未点击开始）。
2. 打开您的 GitHub 仓库，点击顶部的 **Actions** 标签页。
3. 在左侧列表点击 **Hyperliquid Wallet Tracker**。
4. 点击右侧的 **Run workflow** 下拉菜单，然后点击绿色的 **Run workflow** 按钮手动触发一次执行。
5. 约 1 分钟后，您的 Telegram 将收到一条 `🤖 Hyperliquid 监控机器人初始化成功！` 的消息，表示已全部配置成功！
6. 此后，GitHub 就会每 30 分钟为您自动扫描一次并汇总推送。

---

## ✏️ 以后如何添加或删除监控地址？

只需在本地或直接在 GitHub 网站上编辑 `hl_tracker_config.json` 文件，更新 `monitored_addresses` 地址列表即可。格式如下：

```json
{
  "monitored_addresses": [
    {
      "address": "0x第一人地址",
      "label": "聪明钱A"
    },
    {
      "address": "0x第二人地址",
      "label": "巨鲸B"
    }
  ]
}
```
保存并提交（Commit）修改后，机器人下一次运行就会自动加载新配置。
