// front_end/src/components/ui/help-content.tsx
"use client";

import React from "react";

// ============================================================================
// Shared Components - 确保跨模块风格统一
// ============================================================================

interface HelpSectionProps {
  title: string;
  children: React.ReactNode;
}

function HelpSection({ title, children }: HelpSectionProps) {
  return (
    <div className="bg-black/30 rounded-lg border border-zinc-800/50 p-5 space-y-3">
      <h4 className="text-zinc-200 font-semibold text-sm">{title}</h4>
      {children}
    </div>
  );
}


interface HelpFieldProps {
  name: React.ReactNode;
  color?: string;
  children: React.ReactNode;
}

function HelpField({ name, color, children }: HelpFieldProps) {
  const colorClass = color || "text-zinc-200";
  const nameElement = typeof name === "string"
    ? <span className={`${colorClass} font-medium`}>{name}</span>
    : name;
  return (
    <li>
      {nameElement}
      <span className="mx-1.5">—</span>
      {children}
    </li>
  );
}


interface HelpFieldListProps {
  children: React.ReactNode;
}

function HelpFieldList({ children }: HelpFieldListProps) {
  return (
    <ul className="text-zinc-400 text-xs space-y-2.5">
      {children}
    </ul>
  );
}


interface HelpOrderedListProps {
  children: React.ReactNode;
}

function HelpOrderedList({ children }: HelpOrderedListProps) {
  return (
    <ol className="text-zinc-400 text-xs space-y-2 list-decimal list-inside">
      {children}
    </ol>
  );
}


interface HelpCodeBlockProps {
  children: string;
}

function HelpCodeBlock({ children }: HelpCodeBlockProps) {
  return (
    <pre className="bg-black/50 p-4 rounded-lg text-[11px] text-zinc-300 font-mono overflow-x-auto leading-relaxed">
      {children}
    </pre>
  );
}


function HelpInlineCode({ children }: { children: React.ReactNode }) {
  return (
    <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-blue-400 text-[11px]">
      {children}
    </code>
  );
}


function HelpKeyboard({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="bg-zinc-800 px-1.5 py-0.5 rounded text-[10px] font-mono text-zinc-300">
      {children}
    </kbd>
  );
}


function HelpParagraph({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-zinc-400 text-xs leading-relaxed">
      {children}
    </p>
  );
}


function HelpHighlight({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-blue-400 font-medium">{children}</span>
  );
}


// ============================================================================
// Job Form Help
// ============================================================================

export function JobFormHelp() {
  return (
    <>
      <p>
        通过此表单向 Magnus 调度系统提交计算任务。任务将根据优先级和集群资源可用性自动调度执行。
      </p>

      <HelpSection title="必填字段">
        <HelpFieldList>
          <HelpField name="Task Name">
            任务名称，用于在任务列表中标识和搜索。建议使用有意义的命名，如 train-resnet50-epoch100。
          </HelpField>
          <HelpField name="Namespace / Repo Name">
            GitHub 仓库的组织名和仓库名。系统会通过 SSH 拉取代码，确保仓库已配置正确的访问权限。
          </HelpField>
          <HelpField name="Branch / Commit">
            点击 Scan Repository 后可选择分支和具体提交。HEAD 表示使用该分支的最新提交。
          </HelpField>
          <HelpField name="Entry Command">
            任务启动命令，支持多行。每行作为独立命令顺序执行，工作目录为仓库根目录。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="任务优先级">
        <HelpParagraph>
          Magnus 采用四级优先级调度，高优先级任务可抢占低优先级任务的资源：
        </HelpParagraph>
        <HelpFieldList>
          <HelpField name="A1 (Critical)" color="text-red-400">
            最高优先级，适用于紧急任务。不可被抢占，立即获得资源。
          </HelpField>
          <HelpField name="A2 (High)" color="text-orange-400">
            高优先级，日常生产任务的默认选择。不可被抢占。
          </HelpField>
          <HelpField name="B1 (Normal)" color="text-blue-400">
            标准优先级，适用于非紧急的开发和测试。可被 A 类任务抢占，抢占后状态变为 Paused。
          </HelpField>
          <HelpField name="B2 (Low)" color="text-zinc-500">
            低优先级，适用于后台批量任务。可被 A 类任务抢占。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="计算资源配置">
        <HelpFieldList>
          <HelpField name="GPU Accelerator">
            选择 GPU 类型。选择 CPU Only 时 GPU Count 自动设为 0。
          </HelpField>
          <HelpField name="GPU Count">
            请求的 GPU 数量。多卡任务会分配到同一节点的连续 GPU。
          </HelpField>
          <HelpField name="Advanced Options">
            展开可配置 CPU 核心数、内存大小、指定运行用户等高级选项。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="配置复用">
        <HelpParagraph>
          右上角提供配置的导出和导入功能。点击复制按钮可将当前配置导出为 JSON，
          点击粘贴按钮可从剪贴板导入之前保存的配置。支持跨浏览器、跨设备复用配置。
        </HelpParagraph>
      </HelpSection>
    </>
  );
}


// ============================================================================
// Service Form Help
// ============================================================================

export function ServiceFormHelp() {
  return (
    <>
      <p>
        弹性服务 (Elastic Service) 是独立于任务调度器的长期运行服务单元。服务会根据流量自动启停，
        空闲时释放资源，有请求时自动唤醒，实现按需伸缩。
      </p>

      <HelpSection title="服务标识">
        <HelpFieldList>
          <HelpField name="Service Name">
            服务的显示名称，用于在服务列表中识别。
          </HelpField>
          <HelpField name="Service ID">
            服务的唯一标识符，用于 API 调用和 CLI 访问。必须是 URL 安全的小写字符串，
            如 llm-inference、image-gen-v2。创建后不可修改。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="生命周期配置">
        <HelpFieldList>
          <HelpField name="Idle Timeout (分钟)">
            服务空闲多长时间后自动停止。设置较短的超时可节省资源，但会增加冷启动频率。
            建议根据服务的启动时间和使用频率权衡设置。
          </HelpField>
          <HelpField name="Request Timeout (秒)">
            单次请求的最大等待时间。超时后请求会返回错误。对于耗时较长的推理任务，
            应适当增大此值。
          </HelpField>
          <HelpField name="Max Concurrency">
            服务同时处理的最大请求数。超出并发限制的请求会排队等待。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="服务生命周期">
        <HelpOrderedList>
          <li>创建服务后，系统自动分配一个固定的端口号 (MAGNUS_PORT)</li>
          <li>首次收到请求时，系统启动底层 SLURM 任务运行服务代码</li>
          <li>服务启动后，后续请求直接转发到运行中的实例</li>
          <li>持续无请求达到 Idle Timeout 后，系统自动终止底层任务释放资源</li>
          <li>再次收到请求时，系统重新启动服务（冷启动）</li>
        </HelpOrderedList>
      </HelpSection>

      <HelpSection title="访问方式">
        <HelpFieldList>
          <HelpField name="CLI">
            <HelpInlineCode>magnus call &lt;service-id&gt; --param value</HelpInlineCode>
          </HelpField>
          <HelpField name="Python SDK">
            <HelpInlineCode>from magnus import call_service</HelpInlineCode>
          </HelpField>
          <HelpField name="HTTP API">
            <HelpInlineCode>POST /api/services/&lt;service-id&gt;/</HelpInlineCode>
          </HelpField>
        </HelpFieldList>
      </HelpSection>
    </>
  );
}


// ============================================================================
// Blueprint Editor Help
// ============================================================================

export function BlueprintEditorHelp() {
  return (
    <>
      <p>
        蓝图 (Blueprint) 实现了「Python 函数即前端表单」的开发模式。
        编写一个带类型注解的 Python 函数，系统自动解析函数签名生成对应的前端表单界面，
        用户填写参数后调用函数生成任务配置并提交执行。
      </p>

      <HelpSection title="运行环境">
        <HelpParagraph>
          蓝图代码在执行时，以下符号<HelpHighlight>已自动导入</HelpHighlight>，无需手动 import：
        </HelpParagraph>
        <div className="bg-black/50 p-4 rounded-lg text-[11px] text-zinc-300 font-mono">
          <span className="text-purple-400">JobSubmission</span>, <span className="text-purple-400">JobType</span>, <span className="text-blue-400">Annotated</span>, <span className="text-blue-400">Literal</span>, <span className="text-blue-400">Optional</span>, <span className="text-blue-400">List</span>, <span className="text-blue-400">Dict</span>, <span className="text-blue-400">Any</span>
        </div>
      </HelpSection>

      <HelpSection title="函数规范">
        <HelpParagraph>
          蓝图代码必须定义一个名为 <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-green-400 text-[11px]">generate_job</code> 的函数，
          返回类型为 <HelpInlineCode>JobSubmission</HelpInlineCode>：
        </HelpParagraph>
        <HelpCodeBlock>{`# 定义参数类型（带元数据）
MyParam = Annotated[str, {
    "label": "参数名称",
    "description": "参数说明",
    "placeholder": "输入提示",
}]

def generate_job(
    required_param: MyParam,
    optional_param: Optional[str] = None,
) -> JobSubmission:
    return JobSubmission(
        task_name="my-task",           # 任务名称
        description=None,              # 任务描述 (可选)
        namespace="Rise-AGI",          # GitHub 组织名
        repo_name="my-repo",           # 仓库名
        branch="main",                 # 分支名
        commit_sha="HEAD",             # 提交 SHA 或 HEAD
        entry_command="python main.py",# 启动命令
        gpu_type="A100",               # GPU 类型
        gpu_count=1,                   # GPU 数量
        job_type=JobType.A2,           # 优先级
        cpu_count=None,                # CPU 核心数 (可选)
        memory_demand=None,            # 内存需求 (可选)
        runner=None,                   # 指定运行用户 (可选)
    )`}</HelpCodeBlock>
      </HelpSection>

      <HelpSection title="支持的参数类型">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-400 border-b border-zinc-800">
              <th className="text-left py-2 font-medium">Python 类型</th>
              <th className="text-left py-2 font-medium">表单控件</th>
              <th className="text-left py-2 font-medium">可用元数据</th>
            </tr>
          </thead>
          <tbody className="text-zinc-400">
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">str</code></td>
              <td className="py-2">文本输入框</td>
              <td className="py-2">placeholder, allow_empty, multi_line, min_lines, color, border_color</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">int</code></td>
              <td className="py-2">整数步进器</td>
              <td className="py-2">min, max</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">float</code></td>
              <td className="py-2">浮点数输入框</td>
              <td className="py-2">min, max, placeholder</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">bool</code></td>
              <td className="py-2">开关</td>
              <td className="py-2">—</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">{`Literal["a", "b"]`}</code></td>
              <td className="py-2">下拉选择器</td>
              <td className="py-2">options (可为每个选项定义 label 和 description)</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">Optional[T]</code></td>
              <td className="py-2">带启用开关的字段</td>
              <td className="py-2">禁用时不传参给函数</td>
            </tr>
            <tr>
              <td className="py-2"><code className="text-blue-400">List[T]</code></td>
              <td className="py-2">可动态增删的列表</td>
              <td className="py-2">支持嵌套基础类型</td>
            </tr>
          </tbody>
        </table>
      </HelpSection>

      <HelpSection title="通用元数据属性">
        <HelpFieldList>
          <HelpField name="label" color="text-green-400">
            字段在表单中显示的名称
          </HelpField>
          <HelpField name="description" color="text-green-400">
            字段的详细说明，显示在输入框下方
          </HelpField>
          <HelpField name="scope" color="text-green-400">
            参数分组，相同 scope 的参数会被归类到同一区域显示
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="代码编辑器快捷键">
        <HelpFieldList>
          <HelpField name={<HelpKeyboard>Tab</HelpKeyboard>}>
            插入 4 个空格的缩进。选中多行时，为所有选中行添加缩进。
          </HelpField>
          <HelpField name={<HelpKeyboard>Shift + Tab</HelpKeyboard>}>
            减少缩进。删除行首最多 4 个空格。选中多行时，为所有选中行减少缩进。
          </HelpField>
          <HelpField name={<HelpKeyboard>Ctrl + /</HelpKeyboard>}>
            注释或取消注释。自动判断当前行或选中行的状态，智能切换。保留原有缩进。
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="参数缓存">
        <HelpParagraph>
          通过 Web 界面成功运行蓝图后，系统会<HelpHighlight>自动保存</HelpHighlight>用户填写的参数值。
          下次打开同一蓝图时，如果蓝图签名未发生变化，会<HelpHighlight>自动恢复</HelpHighlight>上次的参数值，
          无需重复填写。当蓝图代码修改导致参数签名变化时，缓存会自动失效。
        </HelpParagraph>
      </HelpSection>

      <HelpSection title="SDK 调用">
        <HelpParagraph>
          蓝图也可以通过 Python SDK 或 CLI 直接调用：
        </HelpParagraph>
        <HelpCodeBlock>{`from magnus import submit_blueprint, run_blueprint

# Fire & Forget
job_id = submit_blueprint("blueprint-id", args={"param": "value"})

# Submit & Wait
result = run_blueprint("blueprint-id", args={"param": "value"})`}</HelpCodeBlock>
      </HelpSection>
    </>
  );
}


// ============================================================================
// Blueprint Runner Help
// ============================================================================

export function BlueprintRunnerHelp() {
  return (
    <>
      <p>
        此表单根据蓝图定义的参数签名自动生成。填写参数后点击 Launch，
        系统会调用蓝图函数生成任务配置并提交到调度系统执行。
      </p>

      <HelpSection title="字段说明">
        <HelpFieldList>
          <li>
            <span className="text-red-400 font-medium">*</span> 标记的字段为必填项，提交前必须填写有效值。
          </li>
          <li>
            带开关的字段为可选参数 (Optional)。开关关闭时，该参数不会传递给蓝图函数，
            函数将使用默认值。开关开启后必须填写有效值。
          </li>
          <li>
            数字类型字段可能设置了最小值和最大值限制，输入超出范围的值会提示错误。
          </li>
          <li>
            下拉选择字段的选项由蓝图定义，部分选项可能带有说明文字。
          </li>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title="参数缓存">
        <HelpParagraph>
          成功提交任务后，系统会<HelpHighlight>自动保存</HelpHighlight>当前填写的参数值。
          下次打开同一蓝图时，如果蓝图签名未发生变化，会<HelpHighlight>自动恢复</HelpHighlight>上次的参数值，
          无需重复填写。当蓝图代码修改导致参数签名变化时，缓存会自动失效。
        </HelpParagraph>
      </HelpSection>

      <HelpSection title="SDK 调用">
        <HelpParagraph>
          除了通过 Web 界面运行蓝图，也可以通过 Python SDK 或 CLI 调用：
        </HelpParagraph>
        <HelpCodeBlock>{`# Python SDK
from magnus import submit_blueprint, run_blueprint

# 提交后立即返回 job_id (Fire & Forget)
job_id = submit_blueprint("blueprint-id", args={"param": "value"})

# 提交并等待完成 (Submit & Wait)
result = run_blueprint("blueprint-id", args={"param": "value"})

# CLI
magnus submit <blueprint-id> --param value
magnus run <blueprint-id> --param value`}</HelpCodeBlock>
      </HelpSection>

      <HelpSection title="提交流程">
        <HelpOrderedList>
          <li>填写所有必填参数，确保可选参数的开关状态正确</li>
          <li>点击 Launch 按钮提交</li>
          <li>系统验证参数合法性，调用蓝图函数生成 JobSubmission</li>
          <li>任务提交成功后自动跳转到 Jobs 页面</li>
          <li>在 Jobs 页面可查看任务状态、日志和结果</li>
        </HelpOrderedList>
      </HelpSection>
    </>
  );
}
