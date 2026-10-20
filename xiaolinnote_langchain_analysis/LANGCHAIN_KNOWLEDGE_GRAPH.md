# LangChain 0-12 总知识图谱

这张图把 13 篇专题放进一条完整工程链路：先按业务难点选择普通函数、Runnable、Agent 或 LangGraph，再通过 Model、Message、Tool、Memory 和 Runtime 执行任务，最后以测试、追踪、恢复和反馈完成生产闭环。

读图约定：

- `[00]` 到 `[12]` 对应本目录专题编号。
- 实线表示主要调用流、数据流或执行流。
- 虚线表示依赖、影响、约束、复用或迁移关系。
- 网页主要采用 LangChain v1 语境；当前仓库采用 LangChain 0.2.x，图中会显式标出版本边界。
- 同一个节点的出边按出边顺序使用不同颜色，便于区分它连接到的不同概念。
- 箭头文字直接说明传递、分类、约束或反馈；下文节点速查和逐边索引使用节点框显示名称，不使用内部标识。图中框架比较与版本演进不等于一次请求的执行顺序。

## 总图

```mermaid
flowchart TB
    NEED["业务需求<br/>目标 / 成功标准 / 停止条件<br/>权限 / 风险 / 延迟 / 成本"]

    subgraph SELECT["A. 先判断问题，再选择抽象"]
        direction LR
        CONTROL{"下一步由谁决定"}
        SIMPLE["普通函数或业务工作流<br/>步骤固定且不需要 LLM 决策<br/>最便宜、稳定、容易测试"]
        FIXED["Runnable / LCEL<br/>开发时已知数据流<br/>可串行、并行和条件分支"]
        DYNAMIC["LangChain Agent<br/>模型运行时选择 Tool<br/>适合标准模型-工具循环"]
        STATEFUL["LangGraph<br/>显式业务状态与拓扑<br/>复杂路由、恢复、审批、多 Agent"]
        DATAHEAVY["LlamaIndex<br/>数据接入、解析、索引<br/>检索、重排与上下文组织"]
        JVM["JVM 生态选型<br/>AI 能力留在 JVM 业务体系<br/>类型化 Java 接口与依赖注入"]
        OTHERS["其他 Agent 方案<br/>OpenAI Agents SDK / CrewAI<br/>AutoGen / Semantic Kernel<br/>Dify 是低代码平台而非同层库"]

        CONTROL -->|"代码和规则"| SIMPLE
        CONTROL -->|"固定组件数据流"| FIXED
        CONTROL -->|"模型动态选工具"| DYNAMIC
        CONTROL -->|"规则与模型共同控制状态"| STATEFUL
        DATAHEAVY -.->|"数据层可独立组合"| DYNAMIC
        JVM -.->|"相同工程问题，不同语言生态"| DYNAMIC
        OTHERS -.->|"按团队、模型和场景比较"| DYNAMIC
    end

    subgraph ARCH["B. LangChain 分层架构与统一协议"]
        direction LR
        CORE["langchain-core<br/>稳定核心协议<br/>Message / Model / Tool / Runnable"]
        MESSAGE["Message<br/>HumanMessage / AIMessage / ToolMessage<br/>tool_call_id 关联请求与结果"]
        MODEL["Model<br/>Chat Model / Embedding Model<br/>厂商响应适配为统一消息"]
        PROMPT["Prompt Template<br/>角色 / 任务 / 上下文 / 变量<br/>运行时生成模型输入"]
        PARSER["Parser 与 Structured Output<br/>字符串解析 / Pydantic Schema<br/>字段合法不等于事实正确"]
        TOOLPROTO["Tool 协议<br/>模型可见调用合同<br/>宿主可执行能力"]
        RUNNABLE["Runnable<br/>统一执行与组合接口<br/>输入 / 输出 / Config Schema"]
        INTEGRATION["独立集成包<br/>langchain-openai 等 Provider 包<br/>模型、向量库与外部服务适配"]
        HIGHLEVEL["langchain 高层能力<br/>create_agent / Middleware<br/>Tools / Structured Output"]
        RUNTIME["LangGraph Runtime<br/>状态、循环、路由、持久化<br/>中断、恢复与事件流"]
        OBSERVE["LangSmith 与可观测性<br/>Trace / Evaluation / Studio<br/>Deployment / Agent Server"]

        CORE -->|定义统一消息| MESSAGE
        CORE -->|定义模型接口| MODEL
        CORE -->|定义工具协议| TOOLPROTO
        CORE -->|定义执行协议| RUNNABLE
        PROMPT -->|构造模型输入| MODEL
        MODEL -->|返回统一消息| MESSAGE
        MESSAGE -->|解析内容或结构| PARSER
        INTEGRATION -->|"实现统一模型与存储接口"| CORE
        HIGHLEVEL -->|"使用核心协议"| CORE
        HIGHLEVEL -->|"编译并运行于"| RUNTIME
        OBSERVE -.->|"贯穿所有调用层"| RUNNABLE
        OBSERVE -.->|追踪Agent生命周期| HIGHLEVEL
        OBSERVE -.->|追踪图状态与执行| RUNTIME
    end

    subgraph CHAIN["C. Chain、Runnable 与 LCEL 固定数据流"]
        direction LR
        CHAINIDEA["Chain 编排思想<br/>输入处理 / Prompt / Model<br/>Retriever / Parser / 业务函数"]
        LCEL["LCEL<br/>使用 | 声明组合关系<br/>组合结果仍是 Runnable"]
        SEQUENCE["RunnableSequence<br/>A -> B -> C 串行传递"]
        PARALLEL["RunnableParallel<br/>同一输入并行执行多分支<br/>结果汇合为字典"]
        BRANCH["RunnableBranch<br/>根据已知条件选择路径"]
        ADAPTER["数据适配原语<br/>RunnableLambda / Passthrough<br/>itemgetter / 显式转换函数"]
        INVOKE["统一调用<br/>invoke / ainvoke<br/>batch / abatch<br/>stream / astream"]
        CROSS["横切能力<br/>with_config / with_retry<br/>with_fallbacks / Tags / Metadata<br/>Callbacks 与 Trace"]
        STREAMLIMIT["能力边界<br/>中间节点不支持流式时会阻塞<br/>统一接口不自动修复类型不匹配"]

        CHAINIDEA -->|以表达式组合步骤| LCEL
        RUNNABLE -.->|"提供组合基础"| LCEL
        LCEL -->|组合串行传递| SEQUENCE
        LCEL -->|组合并行分支| PARALLEL
        LCEL -->|组合条件路由| BRANCH
        ADAPTER -->|"对齐前后步骤类型"| SEQUENCE
        SEQUENCE -->|使用统一执行入口| INVOKE
        PARALLEL -->|使用统一执行入口| INVOKE
        BRANCH -->|使用统一执行入口| INVOKE
        CROSS -.->|"附着于步骤或整条链"| INVOKE
        INVOKE -->|实际能力受节点约束| STREAMLIMIT
    end

    subgraph AGENT["D. Agent 构建、Tool 合同与执行循环"]
        direction LR
        ABOUNDARY["定义边界<br/>目标 / 禁止动作 / 成功与失败<br/>停止条件 / 转人工条件"]
        ACAPABILITY["选择能力<br/>支持 Tool Call 与结构化输出的模型<br/>职责单一的小工具"]
        ACONTRACT["约束行为<br/>system_prompt / 信息边界<br/>response_format / 失败策略"]
        CREATE["组装 Agent<br/>v1 create_agent<br/>model + tools + prompt + response_format"]
        ASTATE["状态与安全<br/>Checkpointer / Store / Middleware<br/>权限 / 幂等 / 人工审批"]
        ACALL["交互方式<br/>同步 / 异步 / Stream<br/>超时 / 取消 / 并发限制"]
        ATEST["验证<br/>Tool 单测 / Agent 轨迹<br/>端到端评测 / Trace 与监控"]

        LOOPMODEL["Model Node<br/>读取 Messages 与可用 Tool Schema"]
        AIMSG["AIMessage<br/>自然语言内容 + 可选 tool_calls"]
        HASTOOL{"包含 Tool Call"}
        TOOLDISPATCH["宿主 Tool Runtime<br/>按名称查找并校验参数<br/>应用程序执行，不是模型执行"]
        TOOLMSG["ToolMessage<br/>工具结果 + 相同 tool_call_id"]
        FINAL["最终回复<br/>自然语言或 Structured Response"]

        TOOLCONTRACT["Tool = 两份合同<br/>模型看到 name / description / args_schema<br/>宿主持有函数、协程或客户端"]
        TOOLLEVEL["Tool 定义层级<br/>普通函数 -> @tool<br/>StructuredTool -> BaseTool<br/>Provider Tool / MCP Tool 按集成适配"]
        TASKARGS["模型可填写的任务参数<br/>关键词 / 城市 / 订单号 / 查询范围"]
        TRUSTARGS["Runtime 注入的可信参数<br/>user_id / tenant_id / role<br/>State / Context / Store / 客户端"]
        TOOLGUARD["执行安全<br/>Pydantic 校验 / 最小权限<br/>超时 / 有界重试 / 幂等 / 审计<br/>参数、业务、瞬时与程序错误分类"]

        ABOUNDARY -->|按目标选择能力| ACAPABILITY
        ACAPABILITY -->|明确行为合同| ACONTRACT
        ACONTRACT -->|组装获准组件| CREATE
        CREATE -->|配置状态和安全| ASTATE
        ASTATE -->|选择交互方式| ACALL
        ACALL -->|验证运行行为| ATEST
        CREATE -->|启动模型工具循环| LOOPMODEL
        LOOPMODEL -->|生成响应消息| AIMSG
        AIMSG -->|检查动作请求| HASTOOL
        HASTOOL -->|"否"| FINAL
        HASTOOL -->|"是"| TOOLDISPATCH
        TOOLCONTRACT -->|提供声明与实现| TOOLDISPATCH
        TOOLLEVEL -->|采用不同定义方式| TOOLCONTRACT
        TASKARGS -->|暴露任务输入Schema| TOOLCONTRACT
        TRUSTARGS -->|注入可信身份依赖| TOOLDISPATCH
        TOOLDISPATCH -->|执行前检查安全| TOOLGUARD
        TOOLGUARD -->|生成实际结果消息| TOOLMSG
        TOOLMSG -->|"写回 Agent State"| LOOPMODEL
    end

    subgraph MEMORY["E. State、Context、短期记忆与长期记忆"]
        direction LR
        STATE["State<br/>执行中持续变化的数据<br/>Messages / 当前步骤 / 工具结果"]
        CONTEXT["Context<br/>本次调用不变的可信依赖<br/>用户 / 租户 / 权限 / 客户端"]
        SHORT["短期记忆<br/>State + thread_id + Checkpointer<br/>属于当前会话线程"]
        CHECKPOINTER["Checkpointer<br/>按 thread_id 保存状态快照<br/>支持续接、暂停与故障恢复"]
        WINDOW["长上下文治理<br/>Trim：可只裁本次输入<br/>Delete：移除当前消息，不等于删除历史快照<br/>Summarize：压缩但可能丢细节"]
        STORE["长期 Store<br/>图状态之外的跨线程数据<br/>namespace + key + JSON value"]
        NAMESPACE["隔离与检索<br/>tenant / user / memory_type<br/>精确读取或语义检索"]
        LONG["长期记忆内容<br/>用户偏好 / 稳定事实 / 历史经验<br/>不是完整聊天记录或实时业务事实"]
        WRITEPOLICY["记忆治理<br/>实时明确写入或后台提炼<br/>来源 / 时间 / 置信度 / 去重<br/>更正 / 过期 / 导出 / 删除 / 脱敏"]
        TOOLRUNTIME["ToolRuntime<br/>runtime.state<br/>runtime.context<br/>runtime.store"]

        STATE -->|构成线程内记忆| SHORT
        SHORT -->|持久化线程状态| CHECKPOINTER
        CHECKPOINTER -.->|"恢复相同 thread_id"| STATE
        STATE -->|管理模型可见历史| WINDOW
        STORE -->|按作用域组织数据| NAMESPACE
        NAMESPACE -->|按授权读写记忆| LONG
        WRITEPOLICY -->|约束保存与删除| STORE
        STATE -->|提供当前工作状态| TOOLRUNTIME
        CONTEXT -->|提供本次可信依赖| TOOLRUNTIME
        STORE -->|提供跨线程数据访问| TOOLRUNTIME
        TOOLRUNTIME -.->|"向 Tool 提供不可见于模型的依赖"| TRUSTARGS
    end

    subgraph GRAPH["F. LangChain 与 LangGraph 的控制层关系"]
        direction LR
        HIGHAGENT["LangChain 高层 Agent<br/>预构建 Model-Tool Loop<br/>以 messages 为默认状态核心<br/>通过 Middleware 扩展生命周期"]
        LOWGRAPH["LangGraph 低层运行时<br/>开发者拥有完整业务拓扑<br/>步骤可以是模型、Tool 或普通函数"]
        GRAPHAPI["Graph API<br/>StateGraph / State / Node / Edge<br/>条件边 / 循环 / 输入输出 Schema"]
        FUNCTIONAL["Functional API<br/>@entrypoint / @task<br/>保留 if / for / 普通函数控制流"]
        COMMAND["控制原语<br/>Command：更新状态并改道<br/>Send：运行时动态 Fan-Out<br/>Subgraph：模块化与多 Agent"]
        REDUCER["Reducer<br/>定义并行状态更新的合并语义<br/>无合并规则的同字段并发写通常报错"]
        DURABLE["Persistence 与 Durable Execution<br/>Checkpoint / 节点任务结果<br/>配持久后端才可跨进程恢复"]
        HITL["Human-in-the-Loop<br/>interrupt 暂停并保存状态<br/>Command(resume=...) 恢复<br/>高层也可用审批 Middleware"]
        FAULT["节点级故障治理<br/>Retry Policy / Timeout / Error Handler<br/>降级 / 补偿 / 人工接管"]
        TIMETRAVEL["Time Travel<br/>从旧 Checkpoint 重放或分叉<br/>不会撤销已经发生的现实副作用"]
        STREAM["多层事件流<br/>messages / values / updates / custom<br/>checkpoints / tasks / debug"]
        IDEMPOTENT["恢复前提<br/>节点可能重新执行<br/>外部写操作必须有业务幂等键<br/>Checkpoint 不等于 Exactly-Once"]

        HIGHAGENT -->|"create_agent 编译为图"| LOWGRAPH
        LOWGRAPH -->|显式定义节点与边| GRAPHAPI
        LOWGRAPH -->|保留代码式控制流| FUNCTIONAL
        GRAPHAPI -->|使用动态控制能力| COMMAND
        COMMAND -->|并行更新需定义合并| REDUCER
        LOWGRAPH -->|持久化执行进度| DURABLE
        DURABLE -->|支持暂停与恢复| HITL
        DURABLE -->|支持节点级恢复| FAULT
        DURABLE -->|支持历史重放分叉| TIMETRAVEL
        LOWGRAPH -->|输出多层运行事件| STREAM
        DURABLE -->|重放要求外部幂等| IDEMPOTENT
        HITL -->|恢复也可能重执行| IDEMPOTENT
        FAULT -->|重试不能重复副作用| IDEMPOTENT
    end

    subgraph ECOSYSTEM["G. 框架生态与组合边界"]
        direction LR
        LCFOCUS["LangChain<br/>Model / Message / Tool / Agent<br/>Middleware 与广泛第三方集成<br/>重心是通用 Agent 组装"]
        LIDATA["LlamaIndex 数据链<br/>Connector -> Parse -> Chunk<br/>Index -> Retrieve -> Rerank<br/>Query Engine -> Context"]
        LITOOL["稳定组合边界<br/>把 Query Engine 包装成 Tool<br/>LangChain 决定何时调用<br/>LangGraph 控制外围业务流程"]
        LC4J["LangChain4j<br/>独立 JVM 框架，不是官方移植<br/>ChatModel / EmbeddingModel / Store"]
        AISERVICE["AI Services<br/>Java Interface + Prompt + Tool<br/>Chat Memory + RAG + POJO 输出<br/>类似声明式服务代理"]
        JAVASTACK["Java 工程集成<br/>Spring Boot / Quarkus<br/>Helidon / Micronaut<br/>复用 DI、配置、测试和监控"]
        JBOUNDARY["LangChain4j 边界<br/>Chat Memory 不等于完整 History<br/>抽象不能抹平供应商全部差异<br/>Guardrails 与 Observability 需看成熟度"]

        LCFOCUS -.->|"优势重心不同但能力有交集"| LIDATA
        LIDATA -->|包装检索为工具| LITOOL
        LITOOL -->|接入通用代理| LCFOCUS
        LC4J -->|声明式代理接口| AISERVICE
        AISERVICE -->|融入JVM业务工程| JAVASTACK
        AISERVICE -->|保留供应商与记忆边界| JBOUNDARY
    end

    subgraph VERSION["H. LangChain 版本演进与迁移"]
        direction LR
        EARLY["早期一体化包<br/>大量预制 Chain / Memory<br/>AgentExecutor 与集成混杂"]
        SPLIT["核心与集成拆分<br/>langchain-core 稳定协议<br/>community 与 Provider 包独立迭代"]
        COMPOSE["Runnable + LCEL<br/>从大量专用类转向<br/>少量协议与可组合原语"]
        GRAPHSHIFT["Agent Runtime 转向 LangGraph<br/>隐藏循环变成状态图<br/>获得持久化、恢复与人工介入"]
        V1["LangChain v1 主线<br/>create_agent + Middleware<br/>高层 Agent API 聚焦"]
        CLASSIC["langchain-classic<br/>承接 LLMChain / ConversationChain<br/>旧 Memory 等存量能力"]
        REPOVERSION["当前仓库版本边界<br/>langchain 0.2.17<br/>langchain-openai 0.1.25<br/>langgraph 0.2.76"]
        MIGRATION["安全迁移步骤<br/>锁依赖 -> 阅读迁移指南<br/>逐层替换 -> 回归 Tool / Stream / State<br/>隔离验证副作用 -> 灰度发布"]

        EARLY -->|拆分核心与集成| SPLIT
        SPLIT -->|统一可组合协议| COMPOSE
        COMPOSE -->|引入显式Agent运行时| GRAPHSHIFT
        GRAPHSHIFT -->|聚焦高层代理接口| V1
        EARLY -->|"存量兼容"| CLASSIC
        REPOVERSION -.->|"Runnable 与旧 Agent API 可运行"| COMPOSE
        REPOVERSION -.->|"v1 API 是迁移目标，不能混写运行"| V1
        V1 -->|按目标版本渐进迁移| MIGRATION
        CLASSIC -->|兼容存量并回归| MIGRATION
    end

    subgraph RESEARCH["I. Deep Research 研究型 Agent"]
        direction LR
        DRBOUNDARY["Deep Research 定位<br/>不是核心包中的一个开关<br/>open_deep_research 是参考应用<br/>Deep Agents 是更通用框架"]
        CLARIFY["澄清目标与范围<br/>研究对象 / 时间范围 / 来源要求<br/>成功标准与报告格式"]
        BRIEF["Research Brief<br/>把模糊需求固化为研究合同"]
        SUPERVISOR["Supervisor<br/>拆分相对独立的子课题<br/>决定串行、并行与补搜"]
        MAP["Map：并行 Researcher<br/>隔离上下文，多轮调用搜索<br/>企业检索 / 数据库 / MCP Tool"]
        EVIDENCE["Evidence<br/>Claim / Source / URI / 时间<br/>摘要 / 置信度 / 子课题<br/>保留可追溯事实"]
        COMPRESS["证据治理<br/>压缩 / 去重 / 交叉核验<br/>区分事实、推断与不确定性"]
        GATE{"质量门禁<br/>目标覆盖是否充分<br/>来源是否独立可信<br/>引用是否支持结论<br/>冲突是否处理"}
        REDUCE["Reduce：统一 Writer<br/>汇总证据而非拼接子报告<br/>统一口径并生成引用"]
        REPORT["最终研究报告<br/>结论 + 证据 + 引用<br/>限制与不确定性"]
        BUDGET["宽度与深度预算<br/>并发 Researcher 数量<br/>每分支 Tool 次数 / 补搜轮数<br/>总 Token / 搜索费 / Timeout / Cancel"]
        DRSECURITY["研究安全<br/>网页是不可信输入<br/>防 Prompt Injection / 最小权限<br/>只读 Tool / 密钥隔离 / 高风险人工复核"]
        DREVAL["研究评测<br/>最终答案与执行轨迹<br/>来源覆盖 / 引用正确 / 冲突处理<br/>延迟 / 成本 / 失败恢复"]

        DRBOUNDARY -->|先明确研究边界| CLARIFY
        CLARIFY -->|固化目标与格式| BRIEF
        BRIEF -->|提交研究合同| SUPERVISOR
        SUPERVISOR -->|拆分并委派子课题| MAP
        MAP -->|提交来源与声明| EVIDENCE
        EVIDENCE -->|去重压缩与核验| COMPRESS
        COMPRESS -->|检查证据覆盖与冲突| GATE
        GATE -->|"存在缺口或冲突"| SUPERVISOR
        GATE -->|"满足停止条件"| REDUCE
        REDUCE -->|统一写作与引用| REPORT
        BUDGET -.->|限制拆分与补搜| SUPERVISOR
        BUDGET -.->|限制分支调用并发| MAP
        DRSECURITY -.->|限制来源与动作| MAP
        DRSECURITY -.->|防泄漏并披露限制| REPORT
        REPORT -->|评测产物与轨迹| DREVAL
    end

    subgraph PRODUCTION["J. 生产闭环"]
        direction LR
        SECURITY["确定性安全边界<br/>认证 / 授权 / 租户隔离<br/>最小权限 / 审批 / 数据脱敏"]
        RELIABILITY["可靠性<br/>Timeout / Retry / Fallback / Cancel<br/>幂等 / 补偿 / 持久化 / 恢复"]
        EVALUATION["分层评测<br/>Tool 正确性 -> Agent 轨迹<br/>端到端质量 -> 线上业务指标<br/>固定数据集与版本比较"]
        TELEMETRY["可观测性<br/>模型 / Prompt / Tool / 数据版本<br/>Token / 延迟 / 成本 / 错误<br/>Trace / Event / Audit"]
        FEEDBACK["失败样本回流<br/>修正 Prompt / Tool Schema<br/>补充 Golden Set / Memory 治理<br/>调整 Graph、路由和预算"]

        SECURITY -->|确定恢复的权限边界| RELIABILITY
        RELIABILITY -->|提交可重复执行证据| EVALUATION
        EVALUATION -->|关联运行轨迹| TELEMETRY
        TELEMETRY -->|定位失败并改进| FEEDBACK
    end

    NEED -->|先确定控制权| CONTROL
    FIXED -->|采用固定编排| CHAINIDEA
    DYNAMIC -->|先定义代理边界| ABOUNDARY
    STATEFUL -->|采用显式状态拓扑| LOWGRAPH
    DATAHEAVY -->|聚焦数据检索链| LIDATA
    JVM -->|使用独立Java框架| LC4J

    MODEL -.->|提供模型调用能力| LOOPMODEL
    MESSAGE -.->|提供循环消息协议| LOOPMODEL
    TOOLPROTO -.->|提供工具定义基础| TOOLCONTRACT
    RUNNABLE -.->|复用统一执行接口| CREATE
    PARSER -.->|约束最终输出形状| FINAL
    HIGHLEVEL -.->|提供组装接口| CREATE
    RUNTIME -.->|对应低层运行机制| LOWGRAPH
    CREATE -.->|构建高层Agent| HIGHAGENT
    ASTATE -.->|配置可变工作数据| STATE
    ASTATE -.->|配置可信运行依赖| CONTEXT
    ASTATE -.->|配置长期存储| STORE
    CHECKPOINTER -.->|保存可恢复状态| DURABLE
    MIDDLEWARE["Middleware<br/>动态 Prompt / Model / Tool 选择<br/>消息摘要 / Retry / Guardrail<br/>Human-in-the-Loop"]
    MIDDLEWARE -.->|"包装模型与 Tool 生命周期"| HIGHAGENT
    MIDDLEWARE -.->|"在编译图内部运行，不是独立 Runtime"| LOWGRAPH
    COMMAND -.->|支持动态分发| MAP
    REDUCER -.->|合并并行证据更新| EVIDENCE
    CHECKPOINTER -.->|恢复研究进度| SUPERVISOR
    TOOLLEVEL -.->|提供研究工具| MAP
    LITOOL -.->|复用数据检索| MAP
    STREAM -.->|提供执行事件| TELEMETRY
    OBSERVE -.->|汇集追踪数据| TELEMETRY
    ATEST -.->|提供代理测试证据| EVALUATION
    DREVAL -.->|提供研究评测证据| EVALUATION
    TOOLGUARD -.->|落实动作安全| SECURITY
    CONTEXT -.->|提供可信身份与权限| SECURITY
    NAMESPACE -.->|落实长期数据隔离| SECURITY
    IDEMPOTENT -.->|避免重复外部动作| RELIABILITY
    FAULT -.->|提供有界失败恢复| RELIABILITY
    WRITEPOLICY -.->|反馈记忆治理问题| FEEDBACK
    FEEDBACK -.->|修订目标与停止条件| ABOUNDARY
    FEEDBACK -.->|改进研究合同| BRIEF

    %% Same-source outgoing edges use ordinal colors.

    classDef entry fill:#fff4bf,stroke:#8b7500,color:#302900,stroke-width:2px;
    classDef selection fill:#eaf3ff,stroke:#34699a,color:#13293d;
    classDef architecture fill:#e9f7f7,stroke:#347d7d,color:#153838;
    classDef chain fill:#fff1e6,stroke:#a65f26,color:#4a2811;
    classDef agent fill:#eaf8ee,stroke:#3f7d4e,color:#173c22;
    classDef memory fill:#f4edff,stroke:#7451a6,color:#2f1f49;
    classDef graphLayer fill:#fcecef,stroke:#a64c62,color:#4a1e2a;
    classDef ecosystem fill:#edf5d8,stroke:#66803a,color:#293715;
    classDef version fill:#f1f1f1,stroke:#666666,color:#252525;
    classDef research fill:#eaf0ff,stroke:#4d67a5,color:#1d2d55;
    classDef production fill:#fff0f0,stroke:#9d4b4b,color:#491d1d;

    class NEED entry;
    class CONTROL,SIMPLE,FIXED,DYNAMIC,STATEFUL,DATAHEAVY,JVM,OTHERS selection;
    class CORE,MESSAGE,MODEL,PROMPT,PARSER,TOOLPROTO,RUNNABLE,INTEGRATION,HIGHLEVEL,RUNTIME,OBSERVE architecture;
    class CHAINIDEA,LCEL,SEQUENCE,PARALLEL,BRANCH,ADAPTER,INVOKE,CROSS,STREAMLIMIT chain;
    class ABOUNDARY,ACAPABILITY,ACONTRACT,CREATE,ASTATE,ACALL,ATEST,LOOPMODEL,AIMSG,HASTOOL,TOOLDISPATCH,TOOLMSG,FINAL,TOOLCONTRACT,TOOLLEVEL,TASKARGS,TRUSTARGS,TOOLGUARD agent;
    class STATE,CONTEXT,SHORT,CHECKPOINTER,WINDOW,STORE,NAMESPACE,LONG,WRITEPOLICY,TOOLRUNTIME memory;
    class HIGHAGENT,LOWGRAPH,GRAPHAPI,FUNCTIONAL,COMMAND,REDUCER,DURABLE,HITL,FAULT,TIMETRAVEL,STREAM,IDEMPOTENT,MIDDLEWARE graphLayer;
    class LCFOCUS,LIDATA,LITOOL,LC4J,AISERVICE,JAVASTACK,JBOUNDARY ecosystem;
    class EARLY,SPLIT,COMPOSE,GRAPHSHIFT,V1,CLASSIC,REPOVERSION,MIGRATION version;
    class DRBOUNDARY,CLARIFY,BRIEF,SUPERVISOR,MAP,EVIDENCE,COMPRESS,GATE,REDUCE,REPORT,BUDGET,DRSECURITY,DREVAL research;
    class SECURITY,RELIABILITY,EVALUATION,TELEMETRY,FEEDBACK production;

    linkStyle 0,4,5,6,7,11,12,13,14,15,17,20,22,25,26,27,28,29,30,31,32,33,34,35,36,38,39,40,42,43,44,45,46,47,48,49,50,51,53,54,55,57,60,61,63,64,66,71,72,73,74,75,76,77,79,80,81,82,84,86,87,88,89,90,91,92,93,94,95,97,98,100,102,103,104,105,106 stroke:#2563eb,stroke-width:2px;
    linkStyle 1,8,16,18,23,37,41,52,58,62,67,78,83,85,96,99,101 stroke:#0f766e,stroke-width:2px;
    linkStyle 2,9,19,24,56,65,68 stroke:#16a34a,stroke-width:2px;
    linkStyle 3,10,69,70 stroke:#ca8a04,stroke-width:2px;
    linkStyle 21,59,107,108,109,110,115,117,119,125,128,132,134,135,139,142 stroke:#2563eb,stroke-width:3px;
    linkStyle 111,112,113,114,116,121,124,126,127,130,131,136,137,138,140,141,143 stroke:#0f766e,stroke-width:3px;
    linkStyle 118,120,122,129 stroke:#16a34a,stroke-width:3px;
    linkStyle 123,133 stroke:#ca8a04,stroke-width:3px;
```

## 五条复习主线

1. **固定流程怎么组合**：“Chain 编排思想 → LCEL → RunnableSequence / RunnableParallel / RunnableBranch → 统一调用”；“Runnable → LCEL”说明组合所依赖的统一协议。
2. **标准 Agent 怎么运行**：`[03-05] Message + Model + Tool Schema -> AIMessage.tool_calls -> 宿主执行 -> ToolMessage -> 模型继续判断`。
3. **状态和记忆放哪里**：`[06] 当前线程放 State + Checkpointer，可信身份放 Context，跨线程偏好放 Store`。
4. **何时下沉 LangGraph**：`[09-10] 标准 Agent Loop 不足以表达复杂业务拓扑时，直接设计 State / Node / Edge / Reducer / Interrupt / Recovery`。
5. **研究型 Agent 怎么闭环**：`[12] Clarify -> Brief -> Supervisor -> 并行 Researcher -> Evidence Gate -> 统一 Writer -> 评测与补搜`。

## 最容易混淆的关系

| 概念 | 正确关系 |
| --- | --- |
| Chain、Runnable、LCEL | Chain 是编排思想；Runnable 是统一执行协议；LCEL 是组合 Runnable 的声明式表达方式。 |
| Chain 与 Agent | Chain 的拓扑通常由代码预先确定；Agent 让模型在运行时决定是否调用 Tool 以及是否继续循环。 |
| LangChain 与 LangGraph | LangChain v1 是高层 Agent 框架，LangGraph 是低层编排运行时；`create_agent` 构建在 LangGraph 上。 |
| Middleware 与 LangGraph Node | Middleware 包装标准 Agent 的模型和 Tool 生命周期；Node/Edge 可以表达任意业务步骤和完整拓扑。 |
| Tool Schema 与权限 | Schema 帮模型选对工具、填对参数；身份、授权、租户隔离和副作用控制必须由宿主应用执行。 |
| Tool 参数与 Runtime 参数 | 搜索词、订单号等任务参数可由模型填写；user_id、role、Store、数据库客户端等可信依赖由 Runtime 注入。 |
| State、Context、Store | State 是运行中变化的数据；Context 是单次调用不变的可信依赖；Store 是跨线程长期数据。 |
| Checkpointer 与 Store | Checkpointer 按 thread_id 保存当前图状态；Store 按 namespace/key 保存跨线程偏好、事实和经验。 |
| Memory 与聊天档案 | Memory 是本次模型需要的上下文；完整聊天历史是产品与审计事实，不能只依赖可裁剪的模型记忆。 |
| Checkpoint 与 Exactly-Once | Checkpoint 能恢复执行，但节点可能重新运行；付款、发信和建单仍必须使用业务幂等键。 |
| LangChain 与 LlamaIndex | 两者都能做 Agent 和 RAG；LangChain 重心是通用 Agent 与 Tool 集成，LlamaIndex 重心是数据和检索链路。 |
| LangChain 与 LangChain4j | LangChain4j 是独立 JVM 框架，不是 Python LangChain 的官方 Java 移植或逐项翻译。 |
| Deep Research 与多次搜索 | Deep Research 是动态拆题、并行搜证、证据门禁、补搜和统一写作的工作流，不是简单增加搜索次数。 |
| Structured Output 与正确性 | Pydantic 或 JSON Schema 能约束输出形状，不能证明事实、权限或业务结论正确。 |
| Stream 与性能 | Stream 改善等待体验，不会自动缩短模型或 Tool 总耗时；中间阻塞节点还会推迟首个输出。 |

## 版本边界

| 语境 | 本图中的代表 API | 使用方式 |
| --- | --- | --- |
| 当前仓库 LangChain 0.2.x | Runnable、LCEL、`@tool`、`StructuredTool`、Message、`create_tool_calling_agent`、`AgentExecutor` | 可以结合仓库示例直接运行。 |
| 当前仓库 LangGraph 0.2.x | `StateGraph`、`Send`、`Command`、`MemorySaver` | 可以结合仓库的 LangGraph 示例直接运行。 |
| LangChain v1 主线 | `create_agent`、Middleware、`ToolRuntime`、Checkpointer、Store | 表示网页和当前官方架构方向，不能直接混入 0.2.x 示例。 |
| 存量兼容 | `LLMChain`、`ConversationChain`、旧 Memory | 理解旧项目并渐进迁移；v1 中主要进入 `langchain-classic`。 |

迁移不是批量替换 Import。需要锁定 Core、LangGraph 和 Provider 包的兼容版本，并分别回归 Tool Call、Structured Output、Stream、Checkpoint、Memory 与有副作用路径。

## 框架选型速查

| 主要难点 | 优先考虑 | 原因 |
| --- | --- | --- |
| 固定 Prompt、模型、解析器或检索数据流 | 普通函数或 Runnable/LCEL | 控制明确，测试和追踪简单。 |
| 模型需要从少量 Tools 中动态选择 | LangChain Agent | 高层接口已经提供标准 Model-Tool Loop。 |
| 多阶段业务流程、复杂并行、跨时恢复和人工审批 | LangGraph | State、拓扑、恢复边界和事件成为一等公民。 |
| 私有文档解析、索引、检索和重排最复杂 | LlamaIndex 或专业 RAG 层 | 抽象重心更接近数据与上下文问题。 |
| Java 领域服务、权限和事务已沉淀在 JVM | LangChain4j 或团队既有 Java AI 框架 | 可以复用 Java 的类型、DI、配置、测试和监控体系。 |
| 开放式、多来源、可拆分且高价值的调研 | Deep Research 架构 | 需要动态规划、证据治理、补搜和统一写作。 |

## 专题编号索引

| 编号 | 专题 |
| --- | --- |
| 00 | [LangChain 学习路线与总览](0_langchain_framework_interview_guide.md) |
| 01 | [Agent 框架定位与选型](1_agent_frameworks_selection.md) |
| 02 | [Chain、Runnable 与 LCEL](2_chain_runnable_lcel.md) |
| 03 | [LangChain 分层架构与 Agent Loop](3_langchain_layered_architecture.md) |
| 04 | [构建 Agent 的七步工程方法](4_build_agent_engineering_steps.md) |
| 05 | [Tool 注册、Runtime 与执行安全](5_tool_registration_runtime_contract.md) |
| 06 | [短期 State 与长期 Store](6_short_long_term_memory.md) |
| 07 | [LangChain 与 LlamaIndex 选型](7_langchain_vs_llamaindex_selection.md) |
| 08 | [LangChain4j 与 Java AI Services](8_langchain4j_java_ai_services.md) |
| 09 | [LangChain 与 LangGraph 控制层](9_langchain_vs_langgraph_control_layers.md) |
| 10 | [LangGraph 显式状态与可靠工作流](10_langgraph_stateful_workflow_advantages.md) |
| 11 | [LangChain 版本演进与迁移](11_langchain_version_evolution_migration.md) |
| 12 | [Deep Research、Map-Reduce 与证据治理](12_deep_research_map_reduce_evidence.md) |

这是一张概念关系图，不表示仓库已经生产实现所有 v1 能力。仓库可运行边界和对应代码请继续查看各专题的“当前项目”章节以及 [目录 README](README.md)。

## 面试准备复习资料

### 1. 分层阅读与整体认知

入口：[节点速查](#review-nodes)、[面试问答](#review-questions)、[贯穿案例与代码](#review-example)、[逐边关系索引](#review-edge-index)。本资料使用图中节点名称，既不让读者记内部标识，也不将 Mermaid 本身当成业务代码。

**一分钟回答：** LangChain 把模型、消息、工具与执行协议组合成应用。固定数据流可用 Runnable/LCEL，模型动态选工具可用 Agent，复杂状态、并行合并、审批与恢复需要显式运行时设计。协议统一不代表业务合同、供应商差异或版本差异消失，可靠性仍由权限、幂等、验证和评测支撑。

**读图层次：** 先按控制权选抽象，再读统一协议和固定组合；之后理解工具循环、状态与图运行时；生态和版本属于选型边界；Deep Research 是把这些机制组合成的研究工作流，不是一个万能开关。

<a id="review-nodes"></a>

### 2. 节点速查

#### 2.1 需求与抽象选择

| 图中节点 | 职责与边界 |
| --- | --- |
| **业务需求** | 定目标、验收、权限、预算和停止条件，先于框架选型。 |
| **下一步由谁决定** | 区分代码控制、模型决策与混合状态控制。 |
| **普通函数或业务工作流** | 固定任务的简单基线，不需要模型就不用强加 Agent。 |
| **Runnable / LCEL** | 开发时已知的组件数据流，可并行和分支。 |
| **LangChain Agent** | 运行时由模型提出工具选择，由宿主执行。 |
| **LangGraph** | 显式状态与拓扑，适合复杂路由、恢复和审批。 |
| **LlamaIndex** | 数据接入、索引与检索是主要难点时的候选。 |
| **JVM 生态选型** | JVM 业务工程里的独立 AI 框架。 |
| **其他 Agent 方案** | 按语言、控制、集成和运维需求比较，不只看名称。 |

#### 2.2 协议与 LCEL

| 图中节点 | 职责与边界 |
| --- | --- |
| **langchain-core** | 核心接口与消息协议，具体供应商实现在集成层。 |
| **Message** | 人、模型、工具的消息对象与调用关联。 |
| **Model** | Chat/Embedding 模型接口，返回值要符合相应协议。 |
| **Prompt Template** | 将变量、角色和上下文装配成输入。 |
| **Parser 与 Structured Output** | 解析和结构约束，不证明事实与权限。 |
| **Tool 协议** | 连接模型可见合同与宿主持有的实现。 |
| **Runnable** | 统一调用、组合和配置协议。 |
| **独立集成包** | 适配模型、向量库与外部服务，需版本兼容。 |
| **langchain 高层能力** | v1 Agent、Middleware 等高层能力，不可混入旧版示例。 |
| **LangGraph Runtime** | 状态执行、循环、持久化与事件。 |
| **LangSmith 与可观测性** | 追踪、评测与相关平台工具；具体服务按部署与版本。 |
| **Chain 编排思想** | 将输入处理、检索、模型和解析组织为步骤。 |
| **LCEL** | 使用表达式声明组合，结果仍遵循 Runnable 协议。 |
| **RunnableSequence** | 把上一步输出交给下一步，输入输出需相容。 |
| **RunnableParallel** | 同一输入传给各分支，汇总为字典，非自动共享中间结果。 |
| **RunnableBranch** | 按预定义条件选择路径，条件本身可调用模型。 |
| **数据适配原语** | Lambda、透传、字段提取等，对齐组件的输入输出。 |
| **统一调用** | invoke/async/batch/stream 等入口，实际行为取决于组件支持。 |
| **横切能力** | 配置、重试、fallback、标签和回调，需明确生效范围。 |
| **能力边界** | 类型不匹配和阻塞步骤不会被统一接口自动消除。 |

#### 2.3 Agent 与工具合同

| 图中节点 | 职责与边界 |
| --- | --- |
| **定义边界** | 目标、禁区、失败和转人工条件。 |
| **选择能力** | 选择实际支持所需工具协议的模型和小工具。 |
| **约束行为** | 定 system prompt、输出与错误策略。 |
| **组装 Agent** | v1 接口示意，按版本接入模型、工具与配置。 |
| **状态与安全** | 存储、权限、幂等与审批共同设计。 |
| **交互方式** | 同步、异步、流式、取消与并发限制。 |
| **验证** | 工具单测、轨迹、端到端和线上评测。 |
| **Model Node** | 根据 Messages 和可用 Schema 请求模型决策。 |
| **AIMessage** | 模型消息，可同时有内容和工具请求，不是工具执行结果。 |
| **包含 Tool Call** | 决定继续工具循环还是进入最终回复路径。 |
| **宿主 Tool Runtime** | 查注册实现、校验和执行，模型不直接调用系统。 |
| **ToolMessage** | 按相同调用 ID 返回成功值或错误。 |
| **最终回复** | 对话产物或结构化响应，业务仍需独立验收。 |
| **Tool = 两份合同** | 模型看描述和参数，宿主持有函数/客户端与权限。 |
| **Tool 定义层级** | 函数、装饰器、StructuredTool、BaseTool 等表达方式。 |
| **模型可填写的任务参数** | 搜索词、题号等；不是可信用户身份。 |
| **Runtime 注入的可信参数** | 由应用提供的租户、角色、客户端和状态。 |
| **执行安全** | 参数、业务、瞬时和程序错误分类与动作约束。 |

#### 2.4 状态、记忆与图运行时

| 图中节点 | 职责与边界 |
| --- | --- |
| **State** | 当前执行中持续变化的数据。 |
| **Context** | 本次调用不变的可信依赖，不能让模型任意覆盖。 |
| **短期记忆** | 当前线程可续接的工作状态，不是完整产品历史。 |
| **Checkpointer** | 保存线程快照，恢复仍需真实身份校验。 |
| **长上下文治理** | 裁输入、删状态和摘要的持久化后果不同。 |
| **长期 Store** | 图状态之外的跨线程数据。 |
| **隔离与检索** | namespace、身份范围和检索规则，不能只用相似度。 |
| **长期记忆内容** | 适合复用的偏好与事实，不是把所有历史永久灌回。 |
| **记忆治理** | 来源、确认、时效、更正和删除。 |
| **ToolRuntime** | 工具访问 state/context/store 的接口语境，与泛称的执行器区分。 |
| **LangChain 高层 Agent** | 预构建工具循环，按版本扩展生命周期。 |
| **LangGraph 低层运行时** | 开发者显式拥有业务拓扑，节点可以不含模型。 |
| **Graph API** | State、Node、Edge 与条件边的图式表达。 |
| **Functional API** | 保留普通代码控制流的持久执行表达，版本需核对。 |
| **控制原语** | Command、Send、Subgraph 分别承载状态/改道、动态分发与模块化。 |
| **Reducer** | 定义并行写同一字段时怎样合并。 |
| **Persistence 与 Durable Execution** | 跨时间和进程保存执行进度，依赖配置正确的持久后端。 |
| **Human-in-the-Loop** | 在明确状态处暂停和恢复，审批应绑定候选版本。 |
| **节点级故障治理** | 分类重试、超时、降级、补偿与人工接管。 |
| **Time Travel** | 从历史快照重放或分叉，不撤销现实外部写入。 |
| **多层事件流** | 区分消息、值、更新和调试事件，不都当用户回答。 |
| **恢复前提** | 任务可能重执行，业务写操作仍需幂等。 |
| **Middleware** | 包装 Agent 生命周期，不能替代完整业务拓扑或授权。 |

#### 2.5 生态、版本与研究

| 图中节点 | 职责与边界 |
| --- | --- |
| **LangChain** | 通用模型、工具和代理组装重心。 |
| **LlamaIndex 数据链** | 文档解析、索引、检索到上下文的组织。 |
| **稳定组合边界** | 将查询引擎包装为 Tool，外层再管理控制权。 |
| **LangChain4j** | 独立 JVM 框架，不是 Python 接口逐项翻译。 |
| **AI Services** | 声明式 Java 服务代理，输出和工具仍需合同。 |
| **Java 工程集成** | 复用 DI、权限、配置、测试和监控。 |
| **LangChain4j 边界** | Memory 不等于 History，供应商差异不会自动消失。 |
| **早期一体化包** | 旧 Chain、Memory、Agent 与集成混合的历史设计。 |
| **核心与集成拆分** | 稳定协议与供应商适配各自演进。 |
| **Runnable + LCEL** | 用少量协议与组合替代大量专用 Chain。 |
| **Agent Runtime 转向 LangGraph** | 将隐藏循环转为有状态执行机制。 |
| **LangChain v1 主线** | 高层 API 聚焦与 Middleware，需兼容新依赖。 |
| **langchain-classic** | 存量能力兼容，不应与现代 API 混装而不验证。 |
| **当前仓库版本边界** | 0.2.x 示例与 v1 架构叙述分开。 |
| **安全迁移步骤** | 锁版本、分层替换、回归副作用与灰度，不是只改 import。 |
| **Deep Research 定位** | 研究应用/框架模式，不是核心包的单个开关。 |
| **澄清目标与范围** | 明确对象、时间、来源和验收。 |
| **Research Brief** | 将研究需求固化为合同。 |
| **Supervisor** | 拆子课题、调度和决定补搜。 |
| **Map：并行 Researcher** | 隔离上下文，多轮收集来源与证据。 |
| **Evidence** | 带 URI、时间和子课题的声明与证据。 |
| **证据治理** | 去重、压缩、交叉核验并区分事实与推断。 |
| **质量门禁** | 检查覆盖、来源与冲突，决定补搜或进入写作。 |
| **Reduce：统一 Writer** | 汇总证据而不是简单拼接子报告。 |
| **最终研究报告** | 结论、引用、限制与不确定性。 |
| **宽度与深度预算** | 并发、调用、补搜与任务级总成本。 |
| **研究安全** | 外部网页是数据，不是权限或系统指令。 |
| **研究评测** | 产物与执行轨迹同时评估。 |

#### 2.6 生产闭环

| 图中节点 | 职责与边界 |
| --- | --- |
| **确定性安全边界** | 认证、授权、最小权限、审批与脱敏。 |
| **可靠性** | 分类恢复、取消、幂等和补偿，不无限重试。 |
| **分层评测** | 工具、轨迹、端到端与业务指标。 |
| **可观测性** | 记录版本、事件、token、延迟、成本和审计。 |
| **失败样本回流** | 修正合同、Schema、记忆和图结构后再回归。 |

<a id="review-questions"></a>

### 3. 面试问答

#### Q01：什么时候不用 Agent？

**面试回答：** 下一步已知且只需稳定变换时，用普通函数或 LCEL 更简单。需要模型动态选择动作才引入 Agent；复杂审批、恢复和并行状态需要显式拓扑。是否调用了 LLM 与是否把控制权交给 LLM 是两件事。

#### Q02：Chain、Runnable、LCEL 怎么区分？

**面试回答：** Chain 是编排思想，Runnable 是统一执行协议，LCEL 是组合表达方式。`prompt | model | parser` 声明数据流，不自动检查前后组件的全部语义；数据适配仍需显式实现与测试。

#### Q03：Parallel 中两个分支怎样共享结果？

**面试回答：** 通常每个分支接收同一输入，结果汇合成字典；一个分支不能假定另一个已执行或能直接读取它的中间值。有依赖就拆成先后步骤；纯并行速度仍受最慢分支、并发限制和下游配额约束。

#### Q04：统一 stream 接口意味着每步都实时吗？

**面试回答：** 不意味着。中间模型、解析器或 Lambda 不支持增量变换时会阻塞，事件流也不都是最终回答。分别测首事件、首用户可见内容与总耗时；重试和取消还需要传播到下游工具。

#### Q05：为什么核心包和集成包拆开？

**面试回答：** 核心承载 Message、Model、Tool、Runnable 等协议，供应商与存储适配独立迭代；这样减少耦合但增加兼容版本管理。统一接口不等于厂商工具协议、流式事件和用量统计完全相同。

#### Q06：Agent 的模型工具循环怎样运行？

**面试回答：** Model Node 生成 AIMessage，宿主检查 tool_calls；有请求则授权执行并产生对应 ToolMessage，写回 State 后再请求模型；没有动作请求则生成最终回复。模型结束不等于业务验收通过，发布等动作仍要 Gate 与审批。

#### Q07：为什么 Tool 有“两份合同”？

**面试回答：** 模型看名称、描述和任务参数，宿主持有函数、客户端与执行权限。模型可填题号，但 user_id、tenant_id、role 与数据库连接来自可信 Context。不能把所有函数参数都公开给模型填写后直接执行。

#### Q08：Pydantic 校验过后还需要什么？

**面试回答：** Schema 检查形状，不检查题目是否属于当前项目、标签是否支持、审批是否有效和写操作是否幂等。Runtime 还要区分参数错、业务拒绝、瞬时失败和程序缺陷，向模型返回可处理、脱敏的结果。

#### Q09：State、Context、Store 怎样落到同一案例？

**面试回答：** State 保存 q-42 的当前候选与步骤；Context 承载应用从真实认证来源注入的用户、项目和客户端；Store 保存跨线程获准复用的偏好。三者生命周期和写权限不同。Context 是载体，不会自动让任意传入值变可信；旧会话里的用户身份不能代替本次认证。

#### Q10：Checkpointer、Store 与聊天历史有什么区别？

**面试回答：** Checkpointer 保存某线程执行状态，Store 保存跨线程记忆，产品历史用于完整显示或审计。模型上下文可裁剪，不能因此删掉应保留的业务事实；命名空间也不能单靠调用者随意填写来实现隔离。

#### Q11：Trim、Delete、Summarize 的差别为什么重要？

**面试回答：** Trim 可只裁本次模型输入；RemoveMessage 更新当前消息状态，但历史 checkpoint、日志和长期 Store 可能仍保留旧内容，不等于隐私数据已彻底删除。Summarize 用有损表示替代部分上下文。工具请求与结果不能任意拆开，审批和精确 ID 也不能只靠摘要复述。完整删除需按存储后端与保留策略单独处理，参见 [LangGraph 消息与 checkpoint 管理](https://docs.langchain.com/oss/python/langgraph/add-memory)。

#### Q12：LangChain 高层 Agent 与 LangGraph Node 什么关系？

**面试回答：** v1 高层 Agent 构建于 LangGraph，提供常见模型工具循环；直接用图 API 则由开发者拥有完整拓扑。Node 可以是普通函数、工具或模型，不是每个节点都是独立 Agent，Middleware 也不等于一个外部调度器。

#### Q13：Reducer 解决什么问题？

**面试回答：** 多分支在同一 superstep 写同一字段时，没有相应 Reducer 的单值通道通常会报并发更新错误，不应依赖“最后写入获胜”。Reducer 定义追加、去重等合并规则；错误的覆盖规则会丢证据，无条件追加旧全量结果会重复。需结合稳定 ID、所有权与顺序无关性设计，它不是万能锁。

#### Q14：Checkpoint 为什么不能保证 exactly-once？

**面试回答：** 外部写入成功而快照未保存时，恢复可能重跑节点。发信、退款、建单应有稳定业务操作 ID、幂等接口和结果查询；Time Travel 分叉也不会撤销已发送的邮件。应把“结果未知”单独建模。

#### Q15：人工审批恢复时要注意什么？

**面试回答：** 审批应绑定候选版本、目标资源、规则与权限；恢复可能重新进入节点，因此中断前的操作要可重放。审批后候选变化或权限撤回时要重新校验，不能让历史批准永久生效。

#### Q16：LlamaIndex 与 LangChain 怎样组合最稳？

**面试回答：** 数据层负责解析、检索、精排与证据，提供清晰 Query Engine/Tool 合同；Agent 决定何时调用，外层图管理业务状态。固定输入输出、来源、权限和错误语义，比直接互相传内部对象更稳定。

#### Q17：LangChain4j 是 Java 版 Python LangChain 吗？

**面试回答：** 它是独立 JVM 框架，有自己的类型化接口和集成设计。团队已有 Java 权限、事务、DI 和监控时可复用生态，但仍需验证 Chat Memory、工具协议与供应商差异，不能按 Python 文档逐项照搬。

#### Q18：怎样避免把 v1 API 混进 0.2.x？

**面试回答：** 锁定实际环境版本，区分架构叙述与可运行代码，按迁移指南分层回归模型消息、工具、流式、状态与副作用。本仓库例子使用现有 LCEL；涉及 create_agent、现代 Middleware/ToolRuntime 的叙述不是旧环境直接运行承诺。

#### Q19：Deep Research 和多次搜索有何不同？

**面试回答：** 它先澄清并固化 Brief，由 Supervisor 拆题，Researcher 隔离检索，保存带来源的证据，经门禁补搜或交给统一 Writer。评测覆盖、冲突、引用与预算，而不只统计搜索次数；并行分支共享总预算但不共享无约束权限。

#### Q20：Trace 很完整为什么还需要 Evaluation？

**面试回答：** Trace 描述发生了什么，Evaluation 判断做得是否正确、完整且值得。漂亮的工具轨迹仍可能用错规则。应保留工具单测、冻结业务样例、独立真值和线上指标，将失败回流到正确合同与节点。

<a id="review-example"></a>

### 4. 贯穿案例与代码：用固定 LCEL 先跑通候选验收

**案例：** 给 q-42 生成合法知识点候选。题目与规则可独立读取，用 RunnableParallel 汇合；生成与验证存在依赖，随后用 RunnableSequence 的组合传递。如果未来要模型反复查证，才加入 Agent；需要暂停审批与故障恢复时，再设计 State、Checkpointer 和业务幂等。

| 图中关系 | 本例怎样体现 |
| --- | --- |
| 下一步由谁决定 → Runnable / LCEL | 程序已经知道读取、生成、验证的顺序。 |
| LCEL → RunnableParallel | 两个分支收到同一输入，各自返回题目和规则。 |
| 数据适配原语 → RunnableSequence | 下游显式接收汇合后的字典，字段合同明确。 |
| 统一调用 → 能力边界 | invoke 能执行，不自动证明业务标签正确。 |
| 确定性安全边界 → 可靠性 | 真实外部工具仍要权限和超时，本地例子不模拟发布。 |

代码使用仓库已有 `langchain-core` 0.2.x 的 Runnable，不调用模型，不需要 Key，不使用 v1-only API。候选选择是确定性测试替身，所以**这是固定工作流，不是自主 Agent**。

```python
from langchain_core.runnables import RunnableLambda, RunnableParallel

def read_question(payload):
    if payload["question_id"] != "q-42":
        raise ValueError("unknown question")
    return {"question_id": "q-42", "expected": "binary_search"}

def read_rules(payload):
    return {"allowed": ["binary_search"], "version": 7}

def propose(payload):
    return {
        "label": payload["question"]["expected"],
        "allowed": payload["rules"]["allowed"],
        "rule_version": payload["rules"]["version"],
    }

def gate(candidate):
    if candidate["label"] not in candidate["allowed"] or candidate["rule_version"] != 7:
        raise ValueError("candidate rejected")
    return {"label": candidate["label"], "status": "accepted_for_review"}

readers = RunnableParallel(
    question=RunnableLambda(read_question),
    rules=RunnableLambda(read_rules),
)
workflow = readers | RunnableLambda(propose) | RunnableLambda(gate)
result = workflow.invoke({"question_id": "q-42"})
assert result == {"label": "binary_search", "status": "accepted_for_review"}
try:
    gate({"label": "unregistered", "allowed": ["binary_search"], "rule_version": 7})
except ValueError:
    pass
else:
    raise AssertionError("invalid label should be rejected")
print(result)
```

输出为 `{'label': 'binary_search', 'status': 'accepted_for_review'}`，并不代表已获人工批准或已发布。实际规则服务要检查项目与有效版本，实际语义质量需要题目证据与独立验证，不只查字符串枚举。

本地阅读：[LCEL 专题](2_chain_runnable_lcel.md)、[状态与长期记忆](6_short_long_term_memory.md)、[LangGraph 运行时](10_langgraph_stateful_workflow_advantages.md)、[仓库现有 LangChain 演示](../run_day25_day28_langchain_demo.py)。最后一个入口会涉及模型配置，不能把本节离线成功扩大为在线服务成功。

### 5. 复习卡与闭卷自测

**复述模板：** “按控制权选抽象，用统一协议组合组件，固定流程与动态决策分开；身份来自 Context，工作进度在 State，跨线程记忆在 Store；并行写入需 Reducer，恢复外部动作需幂等，版本与业务 Gate 不能被框架名替代。”

1. 并行分支为什么不能偷读另一个分支尚未完成的结果？对应 Q03/Q13。
2. 模型输出 JSON 合法但访问别人题目，哪层拒绝？对应 Q07/Q08。
3. 相同 thread_id 是否足以证明恢复者身份？对应 Q09/Q10。
4. 从旧 Checkpoint 重放会撤销邮件吗？对应 Q14/Q15。
5. 搜索次数翻倍却没有覆盖新主题，为什么不算研究质量提升？对应 Q19/Q20。

下方逐边索引对照图中名称与关系文字，每条不同关系单独展示，版本关系、依赖和运行数据流分开阅读。

<a id="review-edge-index"></a>

### 6. 逐边关系索引：直接按节点名称对照总图

本节逐条列出图中关系，不再把多个目标合成一格。源节点与目标节点使用节点框首行名称；关系列对应箭头上的文字。实线可以表示流程、依赖或归属，虚线可以表示约束、影响或可选组合，必须结合具体关系阅读。

覆盖核对：103 个节点，144 条绘制连线，按端点、线型与关系文字去重后为 144 种关系。相同关系的重复绘线只解释一次，不代表要额外执行一次；不改原图的样式编号。

#### 6.1 总入口与跨区节点

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **业务需求** | 先确定控制权 | **下一步由谁决定** | 实线 |
| **Middleware** | 包装模型与 Tool 生命周期 | **LangChain 高层 Agent** | 虚线 |
| **Middleware** | 在编译图内部运行，不是独立 Runtime | **LangGraph 低层运行时** | 虚线 |

#### 6.2 A. 先判断问题，再选择抽象

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **下一步由谁决定** | 代码和规则 | **普通函数或业务工作流** | 实线 |
| **下一步由谁决定** | 固定组件数据流 | **Runnable / LCEL** | 实线 |
| **下一步由谁决定** | 模型动态选工具 | **LangChain Agent** | 实线 |
| **下一步由谁决定** | 规则与模型共同控制状态 | **LangGraph** | 实线 |
| **LlamaIndex** | 数据层可独立组合 | **LangChain Agent** | 虚线 |
| **JVM 生态选型** | 相同工程问题，不同语言生态 | **LangChain Agent** | 虚线 |
| **其他 Agent 方案** | 按团队、模型和场景比较 | **LangChain Agent** | 虚线 |
| **Runnable / LCEL** | 采用固定编排 | **Chain 编排思想** | 实线 |
| **LangChain Agent** | 先定义代理边界 | **定义边界** | 实线 |
| **LangGraph** | 采用显式状态拓扑 | **LangGraph 低层运行时** | 实线 |
| **LlamaIndex** | 聚焦数据检索链 | **LlamaIndex 数据链** | 实线 |
| **JVM 生态选型** | 使用独立Java框架 | **LangChain4j** | 实线 |

#### 6.3 B. LangChain 分层架构与统一协议

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **langchain-core** | 定义统一消息 | **Message** | 实线 |
| **langchain-core** | 定义模型接口 | **Model** | 实线 |
| **langchain-core** | 定义工具协议 | **Tool 协议** | 实线 |
| **langchain-core** | 定义执行协议 | **Runnable** | 实线 |
| **Prompt Template** | 构造模型输入 | **Model** | 实线 |
| **Model** | 返回统一消息 | **Message** | 实线 |
| **Message** | 解析内容或结构 | **Parser 与 Structured Output** | 实线 |
| **独立集成包** | 实现统一模型与存储接口 | **langchain-core** | 实线 |
| **langchain 高层能力** | 使用核心协议 | **langchain-core** | 实线 |
| **langchain 高层能力** | 编译并运行于 | **LangGraph Runtime** | 实线 |
| **LangSmith 与可观测性** | 贯穿所有调用层 | **Runnable** | 虚线 |
| **LangSmith 与可观测性** | 追踪Agent生命周期 | **langchain 高层能力** | 虚线 |
| **LangSmith 与可观测性** | 追踪图状态与执行 | **LangGraph Runtime** | 虚线 |
| **Runnable** | 提供组合基础 | **LCEL** | 虚线 |
| **Model** | 提供模型调用能力 | **Model Node** | 虚线 |
| **Message** | 提供循环消息协议 | **Model Node** | 虚线 |
| **Tool 协议** | 提供工具定义基础 | **Tool = 两份合同** | 虚线 |
| **Runnable** | 复用统一执行接口 | **组装 Agent** | 虚线 |
| **Parser 与 Structured Output** | 约束最终输出形状 | **最终回复** | 虚线 |
| **langchain 高层能力** | 提供组装接口 | **组装 Agent** | 虚线 |
| **LangGraph Runtime** | 对应低层运行机制 | **LangGraph 低层运行时** | 虚线 |
| **LangSmith 与可观测性** | 汇集追踪数据 | **可观测性** | 虚线 |

#### 6.4 C. Chain、Runnable 与 LCEL 固定数据流

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **Chain 编排思想** | 以表达式组合步骤 | **LCEL** | 实线 |
| **LCEL** | 组合串行传递 | **RunnableSequence** | 实线 |
| **LCEL** | 组合并行分支 | **RunnableParallel** | 实线 |
| **LCEL** | 组合条件路由 | **RunnableBranch** | 实线 |
| **数据适配原语** | 对齐前后步骤类型 | **RunnableSequence** | 实线 |
| **RunnableSequence** | 使用统一执行入口 | **统一调用** | 实线 |
| **RunnableParallel** | 使用统一执行入口 | **统一调用** | 实线 |
| **RunnableBranch** | 使用统一执行入口 | **统一调用** | 实线 |
| **横切能力** | 附着于步骤或整条链 | **统一调用** | 虚线 |
| **统一调用** | 实际能力受节点约束 | **能力边界** | 实线 |

#### 6.5 D. Agent 构建、Tool 合同与执行循环

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **定义边界** | 按目标选择能力 | **选择能力** | 实线 |
| **选择能力** | 明确行为合同 | **约束行为** | 实线 |
| **约束行为** | 组装获准组件 | **组装 Agent** | 实线 |
| **组装 Agent** | 配置状态和安全 | **状态与安全** | 实线 |
| **状态与安全** | 选择交互方式 | **交互方式** | 实线 |
| **交互方式** | 验证运行行为 | **验证** | 实线 |
| **组装 Agent** | 启动模型工具循环 | **Model Node** | 实线 |
| **Model Node** | 生成响应消息 | **AIMessage** | 实线 |
| **AIMessage** | 检查动作请求 | **包含 Tool Call** | 实线 |
| **包含 Tool Call** | 否 | **最终回复** | 实线 |
| **包含 Tool Call** | 是 | **宿主 Tool Runtime** | 实线 |
| **Tool = 两份合同** | 提供声明与实现 | **宿主 Tool Runtime** | 实线 |
| **Tool 定义层级** | 采用不同定义方式 | **Tool = 两份合同** | 实线 |
| **模型可填写的任务参数** | 暴露任务输入Schema | **Tool = 两份合同** | 实线 |
| **Runtime 注入的可信参数** | 注入可信身份依赖 | **宿主 Tool Runtime** | 实线 |
| **宿主 Tool Runtime** | 执行前检查安全 | **执行安全** | 实线 |
| **执行安全** | 生成实际结果消息 | **ToolMessage** | 实线 |
| **ToolMessage** | 写回 Agent State | **Model Node** | 实线 |
| **组装 Agent** | 构建高层Agent | **LangChain 高层 Agent** | 虚线 |
| **状态与安全** | 配置可变工作数据 | **State** | 虚线 |
| **状态与安全** | 配置可信运行依赖 | **Context** | 虚线 |
| **状态与安全** | 配置长期存储 | **长期 Store** | 虚线 |
| **Tool 定义层级** | 提供研究工具 | **Map：并行 Researcher** | 虚线 |
| **验证** | 提供代理测试证据 | **分层评测** | 虚线 |
| **执行安全** | 落实动作安全 | **确定性安全边界** | 虚线 |

#### 6.6 E. State、Context、短期记忆与长期记忆

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **State** | 构成线程内记忆 | **短期记忆** | 实线 |
| **短期记忆** | 持久化线程状态 | **Checkpointer** | 实线 |
| **Checkpointer** | 恢复相同 thread_id | **State** | 虚线 |
| **State** | 管理模型可见历史 | **长上下文治理** | 实线 |
| **长期 Store** | 按作用域组织数据 | **隔离与检索** | 实线 |
| **隔离与检索** | 按授权读写记忆 | **长期记忆内容** | 实线 |
| **记忆治理** | 约束保存与删除 | **长期 Store** | 实线 |
| **State** | 提供当前工作状态 | **ToolRuntime** | 实线 |
| **Context** | 提供本次可信依赖 | **ToolRuntime** | 实线 |
| **长期 Store** | 提供跨线程数据访问 | **ToolRuntime** | 实线 |
| **ToolRuntime** | 向 Tool 提供不可见于模型的依赖 | **Runtime 注入的可信参数** | 虚线 |
| **Checkpointer** | 保存可恢复状态 | **Persistence 与 Durable Execution** | 虚线 |
| **Checkpointer** | 恢复研究进度 | **Supervisor** | 虚线 |
| **Context** | 提供可信身份与权限 | **确定性安全边界** | 虚线 |
| **隔离与检索** | 落实长期数据隔离 | **确定性安全边界** | 虚线 |
| **记忆治理** | 反馈记忆治理问题 | **失败样本回流** | 虚线 |

#### 6.7 F. LangChain 与 LangGraph 的控制层关系

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **LangChain 高层 Agent** | create_agent 编译为图 | **LangGraph 低层运行时** | 实线 |
| **LangGraph 低层运行时** | 显式定义节点与边 | **Graph API** | 实线 |
| **LangGraph 低层运行时** | 保留代码式控制流 | **Functional API** | 实线 |
| **Graph API** | 使用动态控制能力 | **控制原语** | 实线 |
| **控制原语** | 并行更新需定义合并 | **Reducer** | 实线 |
| **LangGraph 低层运行时** | 持久化执行进度 | **Persistence 与 Durable Execution** | 实线 |
| **Persistence 与 Durable Execution** | 支持暂停与恢复 | **Human-in-the-Loop** | 实线 |
| **Persistence 与 Durable Execution** | 支持节点级恢复 | **节点级故障治理** | 实线 |
| **Persistence 与 Durable Execution** | 支持历史重放分叉 | **Time Travel** | 实线 |
| **LangGraph 低层运行时** | 输出多层运行事件 | **多层事件流** | 实线 |
| **Persistence 与 Durable Execution** | 重放要求外部幂等 | **恢复前提** | 实线 |
| **Human-in-the-Loop** | 恢复也可能重执行 | **恢复前提** | 实线 |
| **节点级故障治理** | 重试不能重复副作用 | **恢复前提** | 实线 |
| **控制原语** | 支持动态分发 | **Map：并行 Researcher** | 虚线 |
| **Reducer** | 合并并行证据更新 | **Evidence** | 虚线 |
| **多层事件流** | 提供执行事件 | **可观测性** | 虚线 |
| **恢复前提** | 避免重复外部动作 | **可靠性** | 虚线 |
| **节点级故障治理** | 提供有界失败恢复 | **可靠性** | 虚线 |

#### 6.8 G. 框架生态与组合边界

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **LangChain** | 优势重心不同但能力有交集 | **LlamaIndex 数据链** | 虚线 |
| **LlamaIndex 数据链** | 包装检索为工具 | **稳定组合边界** | 实线 |
| **稳定组合边界** | 接入通用代理 | **LangChain** | 实线 |
| **LangChain4j** | 声明式代理接口 | **AI Services** | 实线 |
| **AI Services** | 融入JVM业务工程 | **Java 工程集成** | 实线 |
| **AI Services** | 保留供应商与记忆边界 | **LangChain4j 边界** | 实线 |
| **稳定组合边界** | 复用数据检索 | **Map：并行 Researcher** | 虚线 |

#### 6.9 H. LangChain 版本演进与迁移

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **早期一体化包** | 拆分核心与集成 | **核心与集成拆分** | 实线 |
| **核心与集成拆分** | 统一可组合协议 | **Runnable + LCEL** | 实线 |
| **Runnable + LCEL** | 引入显式Agent运行时 | **Agent Runtime 转向 LangGraph** | 实线 |
| **Agent Runtime 转向 LangGraph** | 聚焦高层代理接口 | **LangChain v1 主线** | 实线 |
| **早期一体化包** | 存量兼容 | **langchain-classic** | 实线 |
| **当前仓库版本边界** | Runnable 与旧 Agent API 可运行 | **Runnable + LCEL** | 虚线 |
| **当前仓库版本边界** | v1 API 是迁移目标，不能混写运行 | **LangChain v1 主线** | 虚线 |
| **LangChain v1 主线** | 按目标版本渐进迁移 | **安全迁移步骤** | 实线 |
| **langchain-classic** | 兼容存量并回归 | **安全迁移步骤** | 实线 |

#### 6.10 I. Deep Research 研究型 Agent

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **Deep Research 定位** | 先明确研究边界 | **澄清目标与范围** | 实线 |
| **澄清目标与范围** | 固化目标与格式 | **Research Brief** | 实线 |
| **Research Brief** | 提交研究合同 | **Supervisor** | 实线 |
| **Supervisor** | 拆分并委派子课题 | **Map：并行 Researcher** | 实线 |
| **Map：并行 Researcher** | 提交来源与声明 | **Evidence** | 实线 |
| **Evidence** | 去重压缩与核验 | **证据治理** | 实线 |
| **证据治理** | 检查证据覆盖与冲突 | **质量门禁** | 实线 |
| **质量门禁** | 存在缺口或冲突 | **Supervisor** | 实线 |
| **质量门禁** | 满足停止条件 | **Reduce：统一 Writer** | 实线 |
| **Reduce：统一 Writer** | 统一写作与引用 | **最终研究报告** | 实线 |
| **宽度与深度预算** | 限制拆分与补搜 | **Supervisor** | 虚线 |
| **宽度与深度预算** | 限制分支调用并发 | **Map：并行 Researcher** | 虚线 |
| **研究安全** | 限制来源与动作 | **Map：并行 Researcher** | 虚线 |
| **研究安全** | 防泄漏并披露限制 | **最终研究报告** | 虚线 |
| **最终研究报告** | 评测产物与轨迹 | **研究评测** | 实线 |
| **研究评测** | 提供研究评测证据 | **分层评测** | 虚线 |

#### 6.11 J. 生产闭环

| 源节点 | 关系（图上文字） | 目标节点 | 线型 |
| --- | --- | --- | --- |
| **确定性安全边界** | 确定恢复的权限边界 | **可靠性** | 实线 |
| **可靠性** | 提交可重复执行证据 | **分层评测** | 实线 |
| **分层评测** | 关联运行轨迹 | **可观测性** | 实线 |
| **可观测性** | 定位失败并改进 | **失败样本回流** | 实线 |
| **失败样本回流** | 修订目标与停止条件 | **定义边界** | 虚线 |
| **失败样本回流** | 改进研究合同 | **Research Brief** | 虚线 |
