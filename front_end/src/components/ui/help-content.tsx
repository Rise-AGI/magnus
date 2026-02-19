// front_end/src/components/ui/help-content.tsx
"use client";

import React from "react";
import { useLanguage } from "@/context/language-context";

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
  const { t } = useLanguage();

  return (
    <>
      <p>
        {t("help.jobForm.intro")}
      </p>

      <HelpSection title={t("help.jobForm.requiredFields")}>
        <HelpFieldList>
          <HelpField name="Task Name">
            {t("help.jobForm.taskName")}
          </HelpField>
          <HelpField name="Namespace / Repo Name">
            {t("help.jobForm.namespace")}
          </HelpField>
          <HelpField name="Branch / Commit">
            {t("help.jobForm.branch")}
          </HelpField>
          <HelpField name="Entry Command">
            {t("help.jobForm.entryCommand")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.jobForm.priority")}>
        <HelpParagraph>
          {t("help.jobForm.priorityIntro")}
        </HelpParagraph>
        <HelpFieldList>
          <HelpField name="A1 (Critical)" color="text-red-400">
            {t("help.jobForm.a1")}
          </HelpField>
          <HelpField name="A2 (High)" color="text-orange-400">
            {t("help.jobForm.a2")}
          </HelpField>
          <HelpField name="B1 (Normal)" color="text-blue-400">
            {t("help.jobForm.b1")}
          </HelpField>
          <HelpField name="B2 (Low)" color="text-zinc-500">
            {t("help.jobForm.b2")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.jobForm.resources")}>
        <HelpFieldList>
          <HelpField name="GPU Accelerator">
            {t("help.jobForm.gpuAccelerator")}
          </HelpField>
          <HelpField name="GPU Count">
            {t("help.jobForm.gpuCount")}
          </HelpField>
          <HelpField name="Advanced Options">
            {t("help.jobForm.advancedOptions")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.jobForm.configReuse")}>
        <HelpParagraph>
          {t("help.jobForm.configReuseDesc")}
        </HelpParagraph>
      </HelpSection>

      <HelpSection title={t("help.jobForm.runtimeEnv")}>
        <HelpParagraph>
          {t("help.jobForm.runtimeEnvDesc")}
        </HelpParagraph>
        <HelpCodeBlock>{`/magnus/                     ← $MAGNUS_HOME
└── workspace/               ← bind mount (host → container)
    ├── repository/          ← git repo root (working directory)
    ├── .magnus_result       ← $MAGNUS_RESULT
    └── .magnus_action       ← $MAGNUS_ACTION`}</HelpCodeBlock>
        <HelpFieldList>
          <HelpField name="MAGNUS_TOKEN" color="text-green-400">
            {t("help.jobForm.envToken")}
          </HelpField>
          <HelpField name="MAGNUS_ADDRESS" color="text-green-400">
            {t("help.jobForm.envAddress")}
          </HelpField>
          <HelpField name="MAGNUS_JOB_ID" color="text-green-400">
            {t("help.jobForm.envJobId")}
          </HelpField>
          <HelpField name="MAGNUS_RESULT" color="text-green-400">
            {t("help.jobForm.envResult")}
          </HelpField>
          <HelpField name="MAGNUS_ACTION" color="text-green-400">
            {t("help.jobForm.envAction")}
          </HelpField>
        </HelpFieldList>
        <HelpParagraph>
          {t("help.jobForm.runtimeWritability")}
        </HelpParagraph>
      </HelpSection>

      <HelpSection title={t("help.jobForm.networkIsolation")}>
        <HelpParagraph>
          {t("help.jobForm.networkIsolationDesc")}
        </HelpParagraph>
        <HelpCodeBlock>{`# 桥接模式 + 端口映射（由管理员配置）
MAGNUS_NET_MODE=bridge
MAGNUS_PORT_MAP=0.0.0.0:31000:8888/tcp`}</HelpCodeBlock>
        <HelpParagraph>
          {t("help.jobForm.proxyPassthrough")}
        </HelpParagraph>
      </HelpSection>
    </>
  );
}


// ============================================================================
// Service Form Help
// ============================================================================

export function ServiceFormHelp() {
  const { t } = useLanguage();

  return (
    <>
      <p>
        {t("help.serviceForm.intro")}
      </p>

      <HelpSection title={t("help.serviceForm.identity")}>
        <HelpFieldList>
          <HelpField name="Service Name">
            {t("help.serviceForm.serviceName")}
          </HelpField>
          <HelpField name="Service ID">
            {t("help.serviceForm.serviceId")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.serviceForm.lifecycle")}>
        <HelpFieldList>
          <HelpField name="Idle Timeout">
            {t("help.serviceForm.idleTimeout")}
          </HelpField>
          <HelpField name="Request Timeout">
            {t("help.serviceForm.requestTimeout")}
          </HelpField>
          <HelpField name="Max Concurrency">
            {t("help.serviceForm.maxConcurrency")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.serviceForm.lifecycleFlow")}>
        <HelpOrderedList>
          <li>{t("help.serviceForm.flow1")}</li>
          <li>{t("help.serviceForm.flow2")}</li>
          <li>{t("help.serviceForm.flow3")}</li>
          <li>{t("help.serviceForm.flow4")}</li>
          <li>{t("help.serviceForm.flow5")}</li>
        </HelpOrderedList>
      </HelpSection>

      <HelpSection title={t("help.serviceForm.accessMethods")}>
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
  const { t } = useLanguage();

  return (
    <>
      <p>
        {t("help.blueprintEditor.intro")}
      </p>

      <HelpSection title={t("help.blueprintEditor.runtime")}>
        <HelpParagraph>
          {t("help.blueprintEditor.runtimeDesc").split(t("help.blueprintEditor.autoImported"))[0]}
          <HelpHighlight>{t("help.blueprintEditor.autoImported")}</HelpHighlight>
          {t("help.blueprintEditor.runtimeDesc").split(t("help.blueprintEditor.autoImported"))[1] || ""}
        </HelpParagraph>
        <div className="bg-black/50 p-4 rounded-lg text-[11px] text-zinc-300 font-mono">
          <span className="text-purple-400">JobSubmission</span>, <span className="text-purple-400">JobType</span>, <span className="text-purple-400">FileSecret</span>, <span className="text-blue-400">Annotated</span>, <span className="text-blue-400">Literal</span>, <span className="text-blue-400">Optional</span>, <span className="text-blue-400">List</span>, <span className="text-blue-400">Dict</span>, <span className="text-blue-400">Any</span>
        </div>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.functionSpec")}>
        <HelpParagraph>
          {t("help.blueprintEditor.functionSpecDesc").split("generate_job")[0]}
          <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-green-400 text-[11px]">generate_job</code>
          {t("help.blueprintEditor.functionSpecDesc").split("generate_job")[1]?.split("JobSubmission")[0]}
          <HelpInlineCode>JobSubmission</HelpInlineCode>
          {t("help.blueprintEditor.functionSpecDesc").split("JobSubmission")[1] || ""}
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
        commit_sha="HEAD",             # 提交 SHA / HEAD / msg:正则
        entry_command="python main.py",# 启动命令
        gpu_type="A100",               # GPU 类型
        gpu_count=1,                   # GPU 数量
        job_type=JobType.A2,           # 优先级
        cpu_count=None,                # CPU 核心数 (可选)
        memory_demand=None,            # 内存需求 (可选)
        ephemeral_storage=None,        # 临时存储大小 (可选, 如 "10G")
        runner=None,                   # 指定运行用户 (可选)
    )`}</HelpCodeBlock>
      </HelpSection>

      <HelpSection title="commit_sha Smart Resolution">
        <HelpParagraph>
          <HelpInlineCode>commit_sha</HelpInlineCode> supports three modes:
        </HelpParagraph>
        <HelpFieldList>
          <HelpField name={<HelpInlineCode>&quot;HEAD&quot;</HelpInlineCode>}>
            Latest commit on the specified branch
          </HelpField>
          <HelpField name={<HelpInlineCode>&quot;abc123...&quot;</HelpInlineCode>}>
            Exact 40-char SHA or any valid git ref
          </HelpField>
          <HelpField name={<HelpInlineCode>&quot;msg:&lt;regex&gt;&quot;</HelpInlineCode>}>
            Search for the most recent commit whose message matches the Python regex
          </HelpField>
        </HelpFieldList>
        <HelpCodeBlock>{`# Examples
commit_sha="HEAD"                   # latest on branch
commit_sha="msg:fix.*scheduler"     # most recent commit matching regex
commit_sha="msg:release"              # commit message containing "release"
commit_sha="msg:v2\\.0"              # literal dot (regex escaped)`}</HelpCodeBlock>
        <HelpParagraph>
          The resolved SHA is written back to the database — the original <HelpInlineCode>msg:</HelpInlineCode> value is never stored permanently.
          If no match is found within the most recent 200 commits, the job will fail with a descriptive error.
        </HelpParagraph>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.paramTypes")}>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-zinc-400 border-b border-zinc-800">
              <th className="text-left py-2 font-medium">{t("help.blueprintEditor.pythonType")}</th>
              <th className="text-left py-2 font-medium">{t("help.blueprintEditor.formControl")}</th>
              <th className="text-left py-2 font-medium">{t("help.blueprintEditor.metadata")}</th>
            </tr>
          </thead>
          <tbody className="text-zinc-400">
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">str</code></td>
              <td className="py-2">{t("help.blueprintEditor.textInput")}</td>
              <td className="py-2">placeholder, allow_empty, multi_line, min_lines, color, border_color</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">int</code></td>
              <td className="py-2">{t("help.blueprintEditor.intStepper")}</td>
              <td className="py-2">min, max</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">float</code></td>
              <td className="py-2">{t("help.blueprintEditor.floatInput")}</td>
              <td className="py-2">min, max, placeholder</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">bool</code></td>
              <td className="py-2">{t("help.blueprintEditor.switch")}</td>
              <td className="py-2">—</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">{`Literal["a", "b"]`}</code></td>
              <td className="py-2">{t("help.blueprintEditor.dropdown")}</td>
              <td className="py-2">{t("help.blueprintEditor.optionsDesc")}</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-blue-400">Optional[T]</code></td>
              <td className="py-2">{t("help.blueprintEditor.optionalField")}</td>
              <td className="py-2">{t("help.blueprintEditor.optionalDesc")}</td>
            </tr>
            <tr className="border-b border-zinc-800/50">
              <td className="py-2"><code className="text-purple-400">FileSecret</code></td>
              <td className="py-2">{t("help.blueprintEditor.fileSecretInput")}</td>
              <td className="py-2">placeholder, description</td>
            </tr>
            <tr>
              <td className="py-2"><code className="text-blue-400">List[T]</code></td>
              <td className="py-2">{t("help.blueprintEditor.dynamicList")}</td>
              <td className="py-2">{t("help.blueprintEditor.nestedTypes")}</td>
            </tr>
          </tbody>
        </table>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.commonMetadata")}>
        <HelpFieldList>
          <HelpField name="label" color="text-green-400">
            {t("help.blueprintEditor.labelDesc")}
          </HelpField>
          <HelpField name="description" color="text-green-400">
            {t("help.blueprintEditor.descriptionDesc")}
          </HelpField>
          <HelpField name="scope" color="text-green-400">
            {t("help.blueprintEditor.scopeDesc")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.shortcuts")}>
        <HelpFieldList>
          <HelpField name={<HelpKeyboard>Tab</HelpKeyboard>}>
            {t("help.blueprintEditor.tabDesc")}
          </HelpField>
          <HelpField name={<HelpKeyboard>Shift + Tab</HelpKeyboard>}>
            {t("help.blueprintEditor.shiftTabDesc")}
          </HelpField>
          <HelpField name={<HelpKeyboard>Ctrl + /</HelpKeyboard>}>
            {t("help.blueprintEditor.commentDesc")}
          </HelpField>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.paramCache")}>
        <HelpParagraph>
          {t("help.blueprintEditor.paramCacheDesc")}
        </HelpParagraph>
      </HelpSection>

      <HelpSection title={t("help.blueprintEditor.sdkCall")}>
        <HelpParagraph>
          {t("help.blueprintEditor.sdkCallDesc")}
        </HelpParagraph>
        <HelpCodeBlock>{`from magnus import submit_blueprint, run_blueprint

# Fire & Forget
job_id = submit_blueprint("blueprint-id", args={"param": "value"})

# Submit & Wait
result = run_blueprint("blueprint-id", args={"param": "value"})

# FileSecret: SDK 传文件路径，自动上传
job_id = submit_blueprint("bp-id", args={"data": "my_table.csv"})

# 蓝图内接收文件
from magnus import download_file
download_file(file_secret, "my_table.csv")`}</HelpCodeBlock>
      </HelpSection>
    </>
  );
}


// ============================================================================
// Blueprint Runner Help
// ============================================================================

export function BlueprintRunnerHelp() {
  const { t } = useLanguage();

  return (
    <>
      <p>
        {t("help.blueprintRunner.intro")}
      </p>

      <HelpSection title={t("help.blueprintRunner.fieldNotes")}>
        <HelpFieldList>
          <li>
            <span className="text-red-400 font-medium">*</span> {t("help.blueprintRunner.required")}
          </li>
          <li>
            {t("help.blueprintRunner.optional")}
          </li>
          <li>
            {t("help.blueprintRunner.numberRange")}
          </li>
          <li>
            {t("help.blueprintRunner.dropdown")}
          </li>
          <li>
            <span className="text-purple-400 font-medium">FileSecret</span>
            <span className="mx-1.5">—</span>
            {t("help.blueprintRunner.fileSecret")}
          </li>
        </HelpFieldList>
      </HelpSection>

      <HelpSection title={t("help.blueprintRunner.paramCache")}>
        <HelpParagraph>
          {t("help.blueprintRunner.paramCacheDesc")}
        </HelpParagraph>
      </HelpSection>

      <HelpSection title={t("help.blueprintRunner.sdkCall")}>
        <HelpParagraph>
          {t("help.blueprintRunner.sdkCallDesc")}
        </HelpParagraph>
        <HelpCodeBlock>{`# Python SDK
from magnus import submit_blueprint, run_blueprint

# 提交后立即返回 job_id (Fire & Forget)
job_id = submit_blueprint("blueprint-id", args={"param": "value"})

# 提交并等待完成 (Submit & Wait)
result = run_blueprint("blueprint-id", args={"param": "value"})

# FileSecret 参数：SDK 直接传文件路径
job_id = submit_blueprint("bp-id", args={"data": "/path/to/file"})

# CLI
magnus submit <blueprint-id> --param value
magnus run <blueprint-id> --data /path/to/file`}</HelpCodeBlock>
      </HelpSection>

      <HelpSection title={t("help.blueprintRunner.submitFlow")}>
        <HelpOrderedList>
          <li>{t("help.blueprintRunner.flow1")}</li>
          <li>{t("help.blueprintRunner.flow2")}</li>
          <li>{t("help.blueprintRunner.flow3")}</li>
          <li>{t("help.blueprintRunner.flow4")}</li>
          <li>{t("help.blueprintRunner.flow5")}</li>
        </HelpOrderedList>
      </HelpSection>
    </>
  );
}
