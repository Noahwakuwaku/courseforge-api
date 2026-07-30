# CourseForge API
- Frontend: https://github.com/Noahwakuwaku/courseforge-frontend
- Backend: https://github.com/Noahwakuwaku/courseforge-api

简体中文 | [English](README.md)

CourseForge 是一个 AI 辅助课程体系与学习资料生成器。输入学科名称后，后端会生成课程骨架，判断课程是否需要子分类，生成并审核课程纲要和完整材料，最后创建单选与多选练习题。

本仓库包含 FastAPI 后端与 ARQ Worker；交互界面由独立的 Vue 前端提供。

## 功能

- 生成学科描述和课程体系骨架
- 在适合时将课程展开为子分类
- 通过不同 Agent 生成并审核课程纲要
- 根据学科类型生成完整学习材料
- 生成单选和多选考试题
- 使用 Redis 与 ARQ 执行耗时的 LLM 任务
- 通过 API 单独或批量查询任务状态
- 使用 MongoDB 保存学科、课程、纲要、材料和考试
- 支持内容重新生成与关联数据级联删除
- 限制 LLM 并发量，并重试模型服务的临时错误

## 架构

```text
Vue 前端
    │ HTTP / 任务轮询
    ▼
FastAPI ───────► MongoDB
    │ 提交任务      ▲
    ▼              │ 保存结果
  Redis ───────► ARQ Worker ───────► OpenAI 兼容模型 API
```

## 技术栈

- Python 3.11 或更高版本
- FastAPI 与 Uvicorn
- MongoDB 与 Motor
- Redis 与 ARQ
- OpenAI Python SDK（`AsyncOpenAI`）
- Pydantic Settings

## 环境要求

- Python 3.11+
- MongoDB 6+
- Redis 6+
- OpenAI 兼容模型服务的访问凭据

## 快速开始

克隆仓库并创建虚拟环境：

```bash
git clone <你的后端仓库地址>
cd <后端仓库目录>
python -m venv .venv
```

激活虚拟环境并安装依赖：

```bash
# macOS 或 Linux
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制并编辑环境变量文件：

```bash
cp .env.example .env
```

Windows PowerShell 请使用 `Copy-Item .env.example .env`。

确认 MongoDB 与 Redis 已启动，然后在仓库根目录打开两个终端，分别运行 API 与 Worker：

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

```bash
arq tasks.worker.WorkerSettings
```

API 地址为 <http://localhost:8000/api>，交互式接口文档位于 <http://localhost:8000/docs>。

## 配置

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | `sk-...` | 模型服务 API Key |
| `OPENAI_BASE_URL` | `https://.../compatible-mode/v1` | OpenAI 兼容接口地址 |
| `MODEL_NAME` | `qwen-plus-latest` | 模型服务中的模型名称 |
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB 连接字符串 |
| `MONGO_DB` | `course_gen` | MongoDB 数据库名 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 队列及结果存储地址 |
| `MAX_REVIEW_RETRIES` | `2` | 纲要“生成—审核”的最大尝试次数 |
| `WORKER_MAX_JOBS` | `30` | 单个 Worker 进程的并发任务数 |
| `LLM_MAX_CONCURRENCY` | `20` | 单进程并发模型请求上限 |
| `LLM_MAX_RETRIES` | `3` | 模型服务临时错误的重试次数 |
| `LLM_RETRY_BACKOFF` | `2.0` | 指数退避的基础秒数 |
| `LLM_TIMEOUT` | `180.0` | 单次模型请求超时秒数 |

请勿提交 `.env`。仓库中的 `.env.example` 只包含占位内容。

## 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/subjects` | 生成并保存课程骨架 |
| `GET` | `/api/subjects` | 获取学科列表 |
| `GET` | `/api/subjects/{id}/snapshot` | 加载学科完整视图 |
| `POST` | `/api/subjects/{id}/expand/async` | 提交课程展开任务 |
| `POST` | `/api/courses/{id}/content` | 提交课程纲要生成任务 |
| `POST` | `/api/subcategories/{id}/content` | 提交子分类纲要生成任务 |
| `POST` | `/api/lectures/{id}/material` | 提交学习材料生成任务 |
| `POST` | `/api/materials/{id}/exam` | 提交考试生成任务 |
| `GET` | `/api/tasks/{id}` | 查询单个任务 |
| `POST` | `/api/tasks/batch` | 一次查询多个任务 |

完整且最新的接口列表请查看 `/docs` 中的 OpenAPI 页面。

## 项目结构

```text
agents/                 # 课程骨架、内容、审核、材料与考试 Agent
routers/main_router.py  # HTTP 接口和 ARQ 任务提交
tasks/worker.py         # 当前使用的 ARQ 任务及 Worker 配置
models.py               # MongoDB 存储、索引与级联删除
utils.py                # LLM 客户端、重试、数据库客户端和 JSON 工具
config.py               # 基于环境变量的配置
main.py                 # FastAPI 应用入口
```

## 生产与安全提示

- 将通配符 CORS 改为明确的前端域名白名单。
- 在公开生成与删除接口前加入身份认证和权限控制。
- 不要记录可能包含用户名或密码的数据库连接字符串。
- API 与 Worker 应从同一工作目录启动，或显式指定统一的环境文件，确保两者连接同一个数据库。
- 根据模型服务的 RPM/TPM 限额调整 Worker 与 LLM 并发量。
- 执行管理或删除操作前备份 MongoDB。
- 生成的教育内容用于重要场景前应由人工复核。

## 当前状态

项目目前处于早期阶段，尚未加入自动化测试、身份认证、限流、结构化数据库迁移和生产部署清单。`tasks/celery_app.py` 与 `tasks/generation_tasks.py` 是早期 Celery 实现的遗留文件，当前实际使用的是 ARQ。

## 参与贡献

欢迎提交 Issue 和 Pull Request。请勿提交凭据、数据库导出、生成的 sitemap、缓存或虚拟环境。提交前至少运行一次 Python 语法检查：

```bash
python -m compileall -q .
```

## 许可证


本项目采用 MIT License，详情请参阅 [LICENSE](LICENSE)。

