# Paper Daily

每天自动追踪 arXiv 新论文，按你的研究方向打分，并生成中文论文摘要。项目使用 GitHub Actions 自动抓取论文，用 GitHub Pages 展示网页。页面分成“精选文库”和“顶会精品”两个集合，互不挤占存储上限。

## 每日精选文库（Digest 机器）

本仓库的每日流水线已改造成“精选文库”模式（`scripts/digest.py`），核心思路：每天花费固定预算的劳动力收集候选论文，用 LLM 排序，只把最好的几篇加入一个持续增长、由 git 提交保存的文库（`data/library.json`），网站直接渲染该文库。

每次运行的流程：

1. **候选收集**（总量不超过 `max_labor_paper`，默认 200）：
   - 约 75% 预算：按研究方向抓取最近 1–2 天的 arXiv 新投稿（**前沿** 赛道）。
   - 约 25% 预算：每次轮换一个研究方向，用 Semantic Scholar 免费 API 回溯检索最近 3 年、按引用数排序的经典/基础论文（**经典** 赛道）。
   - 已在文库或历史落选记录中的论文，直接跳过，不占用预算。
2. **两阶段排序**：先用关键词/分类/引用数做零成本粗筛，缩小到约 `shortlist_size`（默认 40）篇；再用 LLM 给每篇的标题+摘要打 0–10 分并给出一句中文理由。**LLM 永远不会给全部 200 篇打分**，控制成本。
3. **入库**：只有得分 ≥ `min_score`（默认 7.0）的论文才有资格入库，按分数取前 `max_daily_added`（默认 5）篇。这是**上限不是配额**——弱势的一天可以一篇都不加。每篇会记录赛道（前沿/经典）、分数、入选理由和入库日期。
4. **审计日志**：进入第二阶段但被拒绝的论文，其分数和理由会追加到 `data/rejected_log.jsonl`，方便回头调阈值。

所有参数（`max_labor_paper`、`max_daily_added`、`min_score`、`shortlist_size` 以及研究方向列表）都集中在 **`config/interests.json`**，改配置不用碰代码。该文件现在是研究方向的唯一事实来源（不再读取 Issue 配置）。

可靠性设计：主定时任务 UTC 01:23，另有 UTC 07:41 的补跑任务；文库索引按 arXiv ID 去重 + 每日入库配额记录在 `data/digest_state.json`，因此重复运行无副作用。每次运行至少会提交一次状态文件，保证仓库始终有活动，GitHub 不会因 60 天不活跃而停用定时任务。抓取/LLM 调用全部带指数退避重试；真正失败时进程以非零退出并在 Actions 里标红，而“正常运行但当天没有论文过线”是绿色成功。

## 你需要配置什么

| 配置         | 必须吗     | 说明                               |
| ------------ | ---------- | ---------------------------------- |
| GitHub Pages | 必须       | 不开启就看不到网页                 |
| 研究方向     | 建议配置   | 不配置会使用仓库自带示例方向       |
| 模型 API Key | 可选但推荐 | 不配置也能抓论文，但摘要会比较基础 |
| 其他运行参数 | 可不配置   | 默认值已经可以直接使用             |

## 第 1 步：Fork 或上传项目

把这个项目 Fork 到你的 GitHub 账号，或者上传到你自己的仓库。

下面假设你的仓库地址是：

```text
https://github.com/你的用户名/你的仓库名
```

## 第 2 步：开启 GitHub Pages

进入你的仓库页面，依次打开：

```text
Settings -> Pages -> Build and deployment -> Source
```

把 `Source` 选择为：

```text
GitHub Actions
```

保存后，网页会由 Actions 自动发布。

运行成功后，你可以在这里看到访问链接：

```text
Settings -> Pages
```

链接通常长这样：

```text
https://你的用户名.github.io/你的仓库名/
```

例如这个仓库对应的形式是：

```text
https://zhouzhao01.github.io/paper-daily/
```

## 手动清空论文缓存

如果之前已经积累了很多低质量或缺少摘要的论文，可以手动清缓存重新跑一次：

1. 打开仓库的 `Actions`。
2. 进入左侧的 `Paper Daily` workflow。
3. 点击右侧 `Run workflow`。
4. 把 `clear_cache` 填成 `true`。
5. 点击绿色的 `Run workflow` 按钮。

这会让本次运行忽略 `web/data/papers.json` 和 `web/data/conference_papers.json` 里的历史论文，重新按当前 Issue 配置抓取。每日新论文默认最多新增和保留 50 篇，顶会精品单独保留，避免两类内容互相挤掉。

## 数据保存方式

GitHub Actions 运行后不会再把 `web/data/*.json` commit 回 `main` 分支。最新论文数据会随 GitHub Pages artifact 发布到网页，同时保存一份到 GitHub Actions cache，下一次运行会先恢复这份缓存再增量更新。

这样可以避免自动更新产生提交、减少 rebase 冲突，也不会让论文数据反复改写仓库历史。注意：Actions cache 属于 GitHub 云端缓存，不适合作为永久数据库；如果你手动清理 Actions cache，下一次运行会从仓库现有数据重新初始化。

## 第 3 步：配置研究方向

推荐用 Issue 配置，后续修改最方便。

1. 打开仓库的 `Issues`。
2. 点击 `New issue`。
3. 选择 `Research Interests` 模板。
4. 修改 JSON 里的 `name`、`description`、`keywords`、`arxiv_categories`。
5. Issue 标题保持为 `Research Interests`。
6. 点击提交。

一个方向大概长这样：

```json
{
  "id": "llm_quantization",
  "name": "大模型低精度量化",
  "description": "关注 LLM 量化、低比特推理、KV cache 量化和推理性能优化。",
  "keywords": [
    "LLM quantization",
    "low-bit quantization",
    "INT4",
    "FP8",
    "KV cache quantization"
  ],
  "arxiv_categories": ["cs.CL", "cs.LG", "cs.AI"]
}
```

新手建议：

- `keywords` 尽量写英文，因为 arXiv 论文标题和摘要主要是英文。
- 每个方向先写 5 到 10 个关键词即可。
- 不确定分类时，可以先用 `cs.CL`、`cs.LG`、`cs.AI`。

### 配置论文来源

默认只启用 arXiv 和你关心的会议源：

- `arxiv`：arXiv API，适合预印本。
- DBLP 会议题录：ISCA、MICRO、HPCA、ASPLOS、MLSys、EuroSys。

Issue JSON 里的默认 `sources` 只需要保留 arXiv：

```json
{
  "sources": [
    { "type": "arxiv", "name": "arXiv" }
  ],
  "topics": []
}
```

如果以后要扩展普通论文来源，可以按需追加这些可选来源：

- `openalex`：OpenAlex Works API，覆盖论文、会议、期刊和机构元数据。
- `crossref`：Crossref Works API，适合 DOI 和期刊/会议元数据。
- `semantic_scholar`：Semantic Scholar Graph API，适合补充摘要、开放 PDF 和引用相关元数据。
- `google_scholar_serpapi`：通过 SerpApi 的 Google Scholar API 搜索，需要 `SERPAPI_API_KEY`。
- `feed`：RSS/Atom，自定义期刊、实验室主页或代理服务。

### 默认会议论文源

仓库默认还会从 DBLP 拉取体系结构和机器学习系统方向的会议题录，不需要登录 ACM、IEEE 或 USENIX：

- 体系结构：ISCA、MICRO、HPCA、ASPLOS
- 机器学习系统和系统：MLSys、EuroSys

默认只抓本年和去年两个会议年。会议论文通常一年更新一次，DBLP 录入也可能比官网发布时间晚一些，所以默认会覆盖最近两届。

如果当前缓存里已经有某个会议某一年的论文，后续运行会直接复用缓存，不会重复请求这一年的 DBLP 题录；超过当前年份窗口的旧会议缓存会被清理。

会议源会先从 DBLP 获取题录、作者、DBLP 链接以及可用的 DOI/出版社链接。对筛选后的会议论文，系统会再按标题依次查询 arXiv、OpenAlex 和 Crossref；如果标题能可靠匹配且来源提供摘要，就回填摘要、PDF 链接和分类，再进入中文分析流程。没有找到摘要的会议论文不会默认让模型凭标题猜创新点。Semantic Scholar 默认不再参与自动搜索，避免频繁 429；如确实需要，可显式开启。

网页里“每日新论文”读取 `web/data/papers.json`，主要服务 arXiv 每日更新；“顶会精品”读取 `web/data/conference_papers.json`，专门保存 ISCA、MICRO、HPCA、ASPLOS、MLSys、EuroSys 这类高质量会议论文。两个文件独立裁剪，顶会论文不会占用每日论文的 50 篇额度。

如果你想在 Issue 里继续追加自己的会议，可以在 JSON 里加 `conference_sources.additional_venues`，默认会议不会被覆盖：

```json
{
  "conference_sources": {
    "additional_venues": [
      {
        "id": "pldi",
        "name": "PLDI",
        "group": "programming languages",
        "dblp_toc_patterns": ["db/conf/pldi/pldi{year}.bht"]
      }
    ]
  },
  "topics": [
    {
      "id": "compiler_systems",
      "name": "编译器系统",
      "description": "关注编译器优化、运行时系统和机器学习系统编译。",
      "keywords": ["compiler optimization", "runtime system", "machine learning compiler"],
      "arxiv_categories": ["cs.PL", "cs.DC"]
    }
  ]
}
```

如果你只想使用自己定义的会议源，可以设置：

```json
{
  "conference_sources": {
    "include_default_venues": false,
    "venues": [
      {
        "id": "pldi",
        "name": "PLDI",
        "group": "programming languages",
        "dblp_toc_patterns": ["db/conf/pldi/pldi{year}.bht"],
        "years": [2026, 2025]
      }
    ]
  },
  "topics": []
}
```

注意：`topics` 不能留空，实际使用时至少保留一个研究方向。

#### 自定义论文网站或期刊网站

推荐优先使用网站提供的 RSS、Atom、OAI、API 或“最新文章订阅”链接，然后配置成 `feed`：

```json
{
  "sources": [
    {
      "type": "feed",
      "name": "Nature Machine Intelligence",
      "url": "https://www.nature.com/natmachintell.rss"
    },
    {
      "type": "feed",
      "name": "自定义实验室论文",
      "url": "https://example.edu/lab/publications.atom"
    }
  ],
  "topics": []
}
```

`feed` 支持 RSS 和 Atom。它适合：

- 期刊 RSS/Atom。
- 会议或 workshop 的 accepted papers feed。
- 实验室、个人主页、机构仓库的论文订阅源。
- 你自己搭建的中转服务，把任意论文网站转换成 RSS/Atom。

如果目标网站只有普通 HTML 页面、需要浏览器渲染、验证码、搜索表单或复杂分页，当前采集器不会直接爬网页。更稳妥的做法是：用网站官方 API/RSS；或自己写一个小的代理服务，把它转换成 RSS/Atom 后再接入 `feed`。

#### 需要登录或 Token 的网站

不要把账号、密码、Cookie、Token 直接写进 Issue JSON 或 `config/interests.json`。这些配置会进入仓库历史或 Issue 页面，不安全。

对于需要认证的 RSS/Atom/API 代理，先在仓库中添加 Secrets：

```text
Settings -> Secrets and variables -> Actions -> Secrets -> New repository secret
```

常用两种方式：

1. Bearer Token：

添加 Secret：

| Name                         | Secret         |
| ---------------------------- | -------------- |
| `CUSTOM_FEED_BEARER_TOKEN` | 你的访问 Token |

然后在 `sources` 中引用这个 Secret 的环境变量名：

```json
{
  "sources": [
    {
      "type": "feed",
      "name": "Private Paper Feed",
      "url": "https://example.com/private/feed.xml",
      "bearer_token_env": "CUSTOM_FEED_BEARER_TOKEN"
    }
  ],
  "topics": []
}
```

采集器请求时会自动加：

```text
Authorization: Bearer <CUSTOM_FEED_BEARER_TOKEN>
```

2. 自定义 HTTP Headers：

添加 Secret：

| Name                    | Secret                       |
| ----------------------- | ---------------------------- |
| `CUSTOM_FEED_HEADERS` | `{"X-API-Key":"你的 key"}` |

然后配置：

```json
{
  "sources": [
    {
      "type": "feed",
      "name": "Authenticated Journal Feed",
      "url": "https://example.com/feed.xml",
      "headers_env": "CUSTOM_FEED_HEADERS"
    }
  ],
  "topics": []
}
```

`CUSTOM_FEED_HEADERS` 必须是 JSON object。也可以包含 Cookie，但不推荐长期依赖 Cookie；Cookie 容易过期，也可能违反目标网站规则。更建议使用官方 API Token 或你自己的代理服务。

#### Google Scholar

Google Scholar 没有稳定官方公开 API，不建议直接爬网页。直接爬 Google Scholar 往往会遇到验证码、封 IP、HTML 结构变化和服务条款风险。

如果确实需要 Google Scholar，有两个推荐方式：

1. 使用 SerpApi：

添加 Secret：

| Name                | Secret           |
| ------------------- | ---------------- |
| `SERPAPI_API_KEY` | 你的 SerpApi Key |

然后在 `sources` 中启用：

```json
{
  "sources": [
    {
      "type": "google_scholar_serpapi",
      "name": "Google Scholar"
    }
  ],
  "topics": []
}
```

2. 使用第三方或自建服务转成 RSS/Atom：

```json
{
  "sources": [
    {
      "type": "feed",
      "name": "Google Scholar Proxy Feed",
      "url": "https://example.com/google-scholar-feed.xml"
    }
  ],
  "topics": []
}
```

#### 访问失败时的行为

每个来源独立运行。某个来源出现超时、429、503、认证失败或格式错误时，会记录 warning 和 `stats.source_stats`，但不会让整个采集流程崩溃。

如果所有来源都失败，并且已有历史论文数据，系统会保留已有数据，避免网页被清空。

可选的 Actions Variables / Secrets：

| Name                                    | 示例                        | 说明                                                                                                                                |
| --------------------------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `PAPER_SOURCES`                       | `arxiv,openalex,crossref` | 未在 JSON 配置`sources` 时使用的默认来源                                                                                          |
| `CONTACT_EMAIL`                       | `you@example.com`         | 提供给 OpenAlex/Crossref 的联系邮箱，进入 polite pool                                                                               |
| `CROSSREF_EMAIL`                      | `you@example.com`         | 只给 Crossref 使用的邮箱                                                                                                            |
| `OPENALEX_EMAIL`                      | `you@example.com`         | 只给 OpenAlex 使用的邮箱                                                                                                            |
| `SEMANTIC_SCHOLAR_API_KEY`            | `...`                     | Semantic Scholar API Key；默认不会使用，需同时设置`ENABLE_SEMANTIC_SCHOLAR=true`                                                  |
| `SERPAPI_API_KEY`                     | `...`                     | 启用`google_scholar_serpapi` 时需要                                                                                               |
| `CUSTOM_FEED_HEADERS`                 | `{"X-API-Key":"..."}`     | 自定义 feed/API 代理需要额外 HTTP headers 时使用，建议配置为 Secret                                                                 |
| `CUSTOM_FEED_BEARER_TOKEN`            | `...`                     | 自定义 feed/API 代理需要 Bearer Token 时使用，建议配置为 Secret                                                                     |
| `MAX_NEW_PAPERS`                      | `50`                      | 每次运行最多新增展示的论文数，避免每天论文过多                                                                                      |
| `MAX_STORED_PAPERS`                   | `50`                      | 网页数据文件最多保留的论文总数                                                                                                      |
| `MAX_NEW_CONFERENCE_PAPERS`           | `50`                      | 每次运行最多新增进入顶会精品库的会议论文数                                                                                          |
| `MAX_STORED_CONFERENCE_PAPERS`        | `300`                     | 顶会精品库最多保留的论文总数，独立于每日论文                                                                                        |
| `MAX_SUMMARIES`                       | `20`                      | 每次最多调用模型生成中文摘要的论文数                                                                                                |
| `CLEAR_PAPER_CACHE`                   | `false`                   | 设为`true` 时忽略历史缓存，重新生成论文列表                                                                                       |
| `MIN_PAPER_SCORE`                     | `0.08`                    | 有摘要论文的最低相关性分数                                                                                                          |
| `MIN_TITLE_ONLY_SCORE`                | `0.18`                    | 只有标题、缺少摘要论文的最低相关性分数                                                                                              |
| `MIN_CONFERENCE_SCORE`                | `0.18`                    | 会议题录没有关键词命中时的最低相关性分数                                                                                            |
| `LLM_SUMMARIZE_CONFERENCE`            | `true`                    | 是否对已有摘要的会议论文调用模型；缺摘要的 DBLP 题录仍不会默认调用                                                                  |
| `LLM_SUMMARIZE_TITLE_ONLY`            | `false`                   | 是否对缺少摘要的论文调用模型；默认关闭，避免标题猜测                                                                                |
| `SOURCE_DELAY_SECONDS`                | `3`                       | 非 arXiv 来源的 topic 请求间隔                                                                                                      |
| `DBLP_DELAY_SECONDS`                  | `5`                       | 不同 DBLP 会议源之间的请求间隔                                                                                                      |
| `DBLP_PATTERN_DELAY_SECONDS`          | `3`                       | 同一会议不同 DBLP TOC pattern 之间的请求间隔                                                                                        |
| `DBLP_RETRIES`                        | `3`                       | DBLP 临时错误的最大尝试次数                                                                                                         |
| `MAX_PER_CONFERENCE`                  | `1000`                    | 每个 DBLP TOC 最多读取的题录数                                                                                                      |
| `ARXIV_QUERY_MODE`                    | `keyword`                 | `keyword` 默认只按关键词抓取，避免分类宽搜淹没相关论文；`broad` 用关键词或分类的单次宽查询；`strict` 使用关键词和分类同时匹配 |
| `ARXIV_SORT_BY`                       | `lastUpdatedDate`         | arXiv 排序字段，默认按最近更新，能捕获当天修订的论文                                                                                |
| `ARXIV_EXPAND_CATEGORY_SEARCH`        | `false`                   | 是否在主查询之外再按相关分类追加一次查询；默认关闭以降低 arXiv 429 风险                                                             |
| `ARXIV_CATEGORY_MAX_RESULTS`          | `10`                      | 每个方向额外按 arXiv 分类抓取的最大数量                                                                                             |
| `MIN_DAILY_PAPERS`                    | `8`                       | 当当天时间窗内 arXiv 论文不足时，至少从最近候选中补足的每日论文数量；设为`0` 可关闭                                               |
| `DAILY_BACKFILL_DAYS`                 | `14`                      | arXiv 每日不足时允许回看最近多少天的候选论文                                                                                        |
| `MAX_CONFERENCE_ABSTRACT_ENRICHMENTS` | `50`                      | 每次最多对多少篇会议候选论文按标题补摘要                                                                                            |
| `CONFERENCE_ABSTRACT_SOURCES`         | `arxiv,openalex,crossref` | 顶会论文摘要回填来源顺序；默认不使用 Semantic Scholar                                                                               |
| `ENABLE_SEMANTIC_SCHOLAR`             | `false`                   | 是否允许`semantic_scholar` 出现在论文/会议摘要搜索链路中                                                                          |
| `CONFERENCE_ABSTRACT_DELAY_SECONDS`   | `3`                       | 会议论文标题查询之间的间隔，避免请求过密                                                                                            |
| `CONFERENCE_ABSTRACT_SEARCH_RESULTS`  | `5`                       | 每个外部来源按标题返回的候选数量                                                                                                    |
| `MAX_CONFERENCE_ARXIV_ENRICHMENTS`    | `50`                      | 旧变量名，未设置`MAX_CONFERENCE_ABSTRACT_ENRICHMENTS` 时作为兼容值                                                                |
| `CONFERENCE_ARXIV_DELAY_SECONDS`      | `3`                       | 旧变量名，未设置`CONFERENCE_ABSTRACT_DELAY_SECONDS` 时作为兼容值                                                                  |
| `CONFERENCE_ARXIV_SEARCH_RESULTS`     | `5`                       | 旧变量名，未设置`CONFERENCE_ABSTRACT_SEARCH_RESULTS` 时作为兼容值                                                                 |
| `ARXIV_RETRY_THROTTLED`               | `false`                   | arXiv 返回 429/503 时默认快速跳过并使用其它来源；设为`true` 会按退避策略等待重试                                                  |
| `ARXIV_RETRIES`                       | `4`                       | arXiv 对非 429/503 临时错误的最大尝试次数                                                                                           |

## 第 4 步：配置模型 API Key！！！！翻译主要看这一步！！

不配置 API Key 也能运行；配置后中文摘要质量会更好。

进入：

```text
Settings -> Secrets and variables -> Actions -> Secrets -> New repository secret
```

如果你用 DeepSeek，添加：

| Name                 | Secret                |
| -------------------- | --------------------- |
| `DEEPSEEK_API_KEY` | 你的 DeepSeek API Key |

如果你用 OpenAI，添加：

| Name               | Secret              |
| ------------------ | ------------------- |
| `OPENAI_API_KEY` | 你的 OpenAI API Key |

如果你用其他 OpenAI-compatible 服务，添加：

| Name            | Secret             |
| --------------- | ------------------ |
| `LLM_API_KEY` | 你的服务商 API Key |

如果你需要指定模型或服务地址，再到：

```text
Settings -> Secrets and variables -> Actions -> Variables -> New repository variable
```

请针对你使用的模型服务配置

| Name             | 示例                            |
| ---------------- | ------------------------------- |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` |
| `LLM_MODEL`    | `deepseek-v4-flash`（DeepSeek 当前可选 `deepseek-v4-flash` / `deepseek-v4-pro`；`deepseek-chat` / `deepseek-reasoner` 将于 2026/07/24 弃用） |

只用 DeepSeek 或 OpenAI 的默认地址时，可以不填这两个变量。

## 第 5 步：第一次手动运行

进入：

```text
Actions -> Paper Daily -> Run workflow
```

第一次建议保持默认：

```text
lookback_days = 7
```

这表示第一次先拉取最近 7 天的相关论文。

点击绿色的 `Run workflow` 后等待运行完成。成功后，打开你的 GitHub Pages 链接即可查看网页：

```text
https://你的用户名.github.io/你的仓库名/
```

## 之后会自动更新

项目默认每天自动运行两次：主任务 UTC 01:23（北京时间 09:23），补跑任务 UTC 07:41（北京时间 15:41）。补跑用于兜底 GitHub 定时任务偶尔不触发的情况；流水线按文库索引去重且每日入库配额共享，重复运行不会产生重复条目。

网页里可以查看：

- 当天拉取的新论文
- 本周论文
- 本月论文
- 本周最相关的精选论文
- 按日期回看本周每天拉取的新论文
- 直接点击 `下载 PDF` 保存论文

## 本地运行（不依赖云端）

整个项目可以完全在本地运行，不需要 GitHub Actions 或 GitHub Pages：

1. 本地生成论文数据（替代 Actions 里的“Collect papers”步骤）：

   ```bash
   python scripts/collect_papers.py
   ```

   本地没有设置 `GITHUB_TOKEN` 时，会自动读取仓库里的 `config/interests.json` 作为研究方向，不再依赖 GitHub Issue。生成的数据写入 `web/data/papers.json` 和 `web/data/conference_papers.json`。

2. 本地预览网页（替代 GitHub Pages）：

   ```bash
   python -m http.server 8000 --directory web
   ```

   浏览器打开：

   ```text
   http://localhost:8000
   ```

数据采集部分是纯标准库实现，本地和云端速度基本一致（主要耗时在 arXiv/DBLP 等网络请求）。区别只在中文摘要用的模型后端，见下一节。

> macOS 提示：用 python.org 安装的 Python 默认没有根证书，所有 HTTPS 请求会报 `SSL: CERTIFICATE_VERIFY_FAILED`。运行前先执行 `/Applications/Python\ 3.12/Install\ Certificates.command`（需要权限），或在命令前加上 `SSL_CERT_FILE=$(python3 -m certifi)`，例如：
>
> ```bash
> SSL_CERT_FILE=$(python3 -m certifi) python scripts/collect_papers.py
> ```

### 用本地 Codex headless 生成摘要

如果你装了 Codex CLI（`codex` 命令）并已登录，可以让摘要直接调用本地 Codex，无需任何模型 API Key：

```bash
LLM_BACKEND=codex python scripts/collect_papers.py
```

可选环境变量：

| Name             | 默认      | 说明                                                       |
| ---------------- | --------- | ---------------------------------------------------------- |
| `LLM_BACKEND`  | `openai` | 设为 `codex` 时改用本地 Codex CLI；也可用 `USE_CODEX_CLI=true` |
| `CODEX_BIN`    | `codex`  | Codex 可执行文件名或路径                                   |
| `CODEX_MODEL`  | 空        | 指定 Codex 使用的模型，留空则用 Codex 默认                 |
| `CODEX_TIMEOUT`| `180`    | 单篇摘要调用的超时时间（秒）                               |
| `LLM_CONCURRENCY`| `2`     | 并发摘要数；用 Codex 时建议设为 `1`，避免同时跑多个进程   |

说明：

- 调用方式是 `codex exec`（headless，read-only 沙箱，不写入仓库），用 `--output-schema` 约束输出为固定 JSON 字段。
- Codex 走的是你已有的订阅授权，质量高、本地可用；但每篇摘要要启动一次 agent，单篇耗时比直接调用云端 API 略长。
- 不设 `LLM_BACKEND=codex` 时，仍可像云端一样用 OpenAI/DeepSeek 兼容 API（设置 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`），或指向本地 OpenAI 兼容服务（如 Ollama 的 `http://localhost:11434/v1`）。
