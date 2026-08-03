# LangGraph 版高级能力实现：方法级注释与图解

对应脚本：`xiaolinnote_agent_analysis/examples/agent_capabilities_langgraph.py`

## 方法级职责说明

- `LangGraphMemoryWorkflow._recall`
  - 职责：按 `tenant_id + user_id` 范围检索长期记忆。
  - 输出：`recalled_memories`，作为后续上下文拼装输入。

- `LangGraphMemoryWorkflow._compress`
  - 职责：执行短期记忆压缩，把历史浓缩到摘要并保留近期窗口。
  - 输出：`messages/summary/working_state/context`。

- `LangGraphDAGOrchestrator._route_schedule`
  - 职责：从当前图状态判断是 `replan / fail / finalize / send-ready-steps`。
  - 关键点：利用 `Send` 并发下发所有 ready 节点。

- `LangGraphDAGOrchestrator._execute_step`
  - 职责：先做 payload binding，再执行 worker，再执行验收。
  - 关键点：技术成功(`ok`)与业务验收(`accepted`)分离。

- `LangGraphDAGOrchestrator._replan`
  - 职责：在预算内追加“未执行的新步骤”，并记录 `plan.revised` 事件。
  - 约束：不允许复用已执行 step_id。

- `LangGraphMultiAgentRouter._route`
  - 职责：静态规则/动态路由选 worker，并通过 guard 校验。
  - 关键点：白名单和 handoff 次数预算控制。

- `LangGraphMultiAgentRouter._worker_node`
  - 职责：执行 worker；若返回 `handoff_to`，则切到 `handoff` 节点。

- `LangGraphReflectionWorkflow._evaluate/_improve/_route_evaluation`
  - 职责：执行 “评估 -> 决策 -> 改进” 的有界循环。
  - 关键点：最后结果必须经过 evaluator 验证，避免“未验证改写”泄漏。

## 流程图（方法级）

```mermaid
flowchart TD
    A[MemoryWorkflow._recall] --> B[MemoryWorkflow._compress]
    B --> C[DAGOrchestrator._route_schedule]
    C -->|ready steps| D[DAGOrchestrator._execute_step]
    D --> C
    C -->|replan| E[DAGOrchestrator._replan]
    E --> C
    C -->|finalize| F[DAGOrchestrator._finalize]
    C -->|fail| G[DAGOrchestrator._fail]

    F --> H[MultiAgentRouter._route]
    H --> I[MultiAgentRouter._worker_node]
    I -->|handoff_to| J[MultiAgentRouter._handoff]
    J --> I
    I -->|completed| K[ReflectionWorkflow._evaluate]
    K -->|improve| L[ReflectionWorkflow._improve]
    L --> K
    K -->|complete/stop| M[Return ReflectionResult]
```

## 时序图（方法级）

```mermaid
sequenceDiagram
    participant Caller
    participant Mem as MemoryWorkflow
    participant DAG as DAGOrchestrator
    participant Router as MultiAgentRouter
    participant Refl as ReflectionWorkflow

    Caller->>Mem: invoke()
    Mem->>Mem: _recall()
    Mem->>Mem: _compress()
    Mem-->>Caller: memory_state

    Caller->>DAG: run(plan)
    loop until done
        DAG->>DAG: _route_schedule()
        alt ready
            DAG->>DAG: _execute_step() x N (Send parallel)
        else need replan
            DAG->>DAG: _replan()
        else fail/finalize
            DAG->>DAG: _fail() or _finalize()
        end
    end
    DAG-->>Caller: dag_result

    Caller->>Router: run(task_type, payload)
    Router->>Router: _route()
    loop optional handoff
        Router->>Router: _worker_node()
        Router->>Router: _handoff()
    end
    Router-->>Caller: routing_result

    Caller->>Refl: run(task, output)
    loop bounded rounds
        Refl->>Refl: _evaluate()
      alt improve
            Refl->>Refl: _improve()
      else stop_or_complete
        Refl->>Refl: keep current best output
        end
    end
    Refl-->>Caller: ReflectionResult
```

## 设计备注

- LangGraph 负责控制面：状态机、循环、并发扇出、路由、检查点。
- 业务逻辑仍通过 worker/replanner/evaluator 注入，便于离线测试与替换。
