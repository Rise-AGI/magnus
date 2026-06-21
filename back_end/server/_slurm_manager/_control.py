# back_end/server/_slurm_manager/_control.py
"""SLURM 任务提交与终止：sbatch / scancel 包装。"""
import math
import os
from typing import Dict, Optional

from .._magnus_config import magnus_config
from .._size_utils import _parse_size_string
from . import logger
from ._transport import _Transport


class _ControlMixin:

    _transport: _Transport

    def submit_job_simple(
        self,
        entry_command: str,
        gpus: int,
        job_name: str,
        runner: str,
        token: str,
        gpu_type: Optional[str] = None,
        output_path: Optional[str] = None,
        overwrite_output: bool = True,
        cpu_count: Optional[int] = None,
        memory_demand: Optional[str] = None,
        time_limit: Optional[int] = None,
    ) -> str:
        """简单提交：不做 sleep + 状态检查，让 SLURM 自己排队和调度。

        sbatch 把 batch script 当作 bash 脚本由 slurmstepd 拉起一个外层 bash
        解释执行 entry_command。batch script 入口装 `trap '' TERM` 让外层 bash
        disposition 设为 SIG_IGN，随后 `exec wrapper.py` 让 wrapper 直接替换
        外层 bash 进程；POSIX 规定 SIG_IGN 通过 exec 继承，所以 wrapper.py 启动
        瞬间到 main() 装上观察用 handler 之间那个时间窗口里 wrapper 也按 SIG_IGN
        处理，不会被默认 disposition 杀。`signal_job` 走 `scancel --signal=TERM
        --batch` 只投递到 wrapper.py 一个 PID（不广播 cgroup 全员，避免 apptainer
        starter / FUSE helpers 等容器基础设施被默认 disposition 杀掉导致
        mount point 崩、user 进程 SIGBUS）；wrapper handler 自己枚举 cgroup，
        按 `/proc/<pid>/status` 的 NSpid 字段筛出 user 容器内进程，对它们
        `kill(2)`。详见 _wrapper_template.py 的 _signal_user_processes 与
        docs/internals/job-runtime.md "Signaling and Termination"。

        约束：`entry_command` 必须是 single simple command（不含 `&&`、`;`、
        `|`、子壳等 shell 复合结构）。`exec <complex>` 在 bash 里只 execve 第一
        个 token，复合结构后半段会被静默吞掉。当前唯一调用点 `_scheduler/_submit.py`
        传 `python3 {wrapper_path}`，满足约束。
        """
        slurm_config = magnus_config["execution"]["slurm"]

        # module_loads：在 batch script 里、exec wrapper 之前注入 `module load` 行，
        # 让租户站点先把容器运行时（singularity）等放上 PATH。module_loads
        # 为空时这段为空串，script_content 与历史字节级一致。`exec` 让 module load 后的
        # 环境（PATH 等）经 POSIX exec 继承给 wrapper.py。
        module_loads = slurm_config["module_loads"]
        module_lines = "".join(
            f"module load {module_name}\n"
            for module_name in module_loads
        )
        script_content = f"#!/bin/bash\ntrap '' TERM\n{module_lines}\nexec {entry_command}"

        command = [
            "sbatch",
            "--parsable",
            f"--job-name={job_name}",
        ]

        # partition / qos / account：租户站点的 SLURM 强制要求这三项；自有
        # 站点配为 None，不下发对应 flag，sbatch 行为与历史一致。
        for flag_name, flag_value in (
            ("partition", slurm_config["partition"]),
            ("qos", slurm_config["qos"]),
            ("account", slurm_config["account"]),
        ):
            if flag_value is not None:
                command.append(f"--{flag_name}={flag_value}")

        log_file = output_path if output_path else "magnus_%j.log"
        command.append(f"--output={log_file}")

        if not overwrite_output:
            command.append("--open-mode=append")

        if gpus > 0:
            if gpu_type and gpu_type != "cpu":
                command.append(f"--gres=gpu:{gpu_type}:{gpus}")
            else:
                command.append(f"--gres=gpu:{gpus}")

        # 内存模式：
        # - explicit（自有站点现状）：直接下发 --mem，与历史一致。
        # - per_cpu（禁用 --mem 的站点）：不发 --mem，把内存需求按
        #   内存 = 核数 × mem_per_cpu_mb 折算成核数，与显式 cpu_count 取较大值，
        #   一并作 --cpus-per-task 下发（核数足够即隐式满足内存需求）。
        effective_cpu_count = cpu_count if (cpu_count is not None and cpu_count > 0) else 0
        if slurm_config["mem_mode"] == "per_cpu":
            if memory_demand is not None:
                mem_per_cpu_mb = slurm_config["mem_per_cpu_mb"]
                memory_demand_mb = _parse_size_string(memory_demand) // (1024 ** 2)
                cores_for_memory = math.ceil(memory_demand_mb / mem_per_cpu_mb)
                effective_cpu_count = max(effective_cpu_count, cores_for_memory)
        else:
            if memory_demand is not None:
                command.append(f"--mem={memory_demand}")
        if effective_cpu_count > 0:
            command.append(f"--cpus-per-task={effective_cpu_count}")

        # time_limit（分钟）→ --time。SLURM 把裸整数当分钟解析。None 时不下发,
        # 沿用分区默认墙钟（自有站点现状字节级不变）。声明短墙钟的任务在共享集群更易
        # 被 backfill 插队。
        if time_limit is not None:
            command.append(f"--time={time_limit}")

        env: Dict[str, str] = os.environ.copy()
        if runner is not None:
            env["MAGNUS_RUNNER"] = runner
        if token is not None:
            env["MAGNUS_TOKEN"] = token

        gpu_info = f"{gpu_type}:{gpus}" if (gpu_type and gpus > 0) else f"{gpus}"
        logger.info(f"🚀 Submitting '{job_name}' to SLURM queue (GPUs: {gpu_info})...")

        result = self._transport.run(
            command,
            input = script_content,
            check = True,
            env = env,
        )

        job_id = result.stdout.strip()
        logger.info(f"✅ Job '{job_name}' queued in SLURM (ID: {job_id})")
        return job_id

    def kill_job(
        self,
        slurm_job_id: str,
        runner: str,
        token: str,
    ) -> None:
        """硬终止 SLURM job：SIGKILL 全员 + scancel 让 SLURM 把 job 移出运行。

        默认的裸 `scancel` 走 KillSignal=SIGTERM、KillWait 秒后才 SIGKILL。
        signal_job 路径下 wrapper.py 装了 handler 收到 SIGTERM 不退、而是按
        NSpid 筛选枚举 cgroup 内 user 容器进程逐个转发，handler-aware 的
        user 代码也会 try graceful shutdown，前 KillWait 秒不一定立刻清场，
        破坏 terminate / 抢占的"瞬时让出 GPU"承诺。直接
        --signal=KILL --full 把 SIGKILL 投给整个 batch step（cgroup 全员）
        立刻清场（SIGKILL 在内核侧不可被 ignore，proctrack 广播覆盖所有 pgrp），
        再裸 scancel 让 SLURM 标记 job 取消。
        """
        env: Dict[str, str] = os.environ.copy()
        env["MAGNUS_RUNNER"] = runner
        env["MAGNUS_TOKEN"] = token

        # Step 1: SIGKILL --full 全员立刻清场
        try:
            result = self._transport.run(
                ["scancel", "--signal=KILL", "--full", slurm_job_id],
                check = False,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel --signal=KILL failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel --signal=KILL failed for job {slurm_job_id}: {error}")

        # Step 2: 裸 scancel 让 SLURM 把 job state 转为 CANCELLED
        try:
            result = self._transport.run(
                ["scancel", slurm_job_id],
                check = False,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel failed for job {slurm_job_id}: {error}")

    def send_signal(
        self,
        slurm_job_id: str,
        signal_name: str,
        runner: str,
        token: str,
    ) -> None:
        """向 SLURM job 的 wrapper.py 发送指定信号但不终止 job。

        `--signal=<sig>` 让 scancel 转为信号转发器（不修改 SLURM 状态）；
        `--batch` 让 SLURM 只把信号投递到 batch step 的 parent process
        —— 即 sbatch script `exec wrapper.py` 之后的 wrapper.py 一个 PID，
        cgroup 内的 apptainer starter / fuse-overlayfs / squashfuse_ll 等
        容器基础设施保持不动（它们没装 handler，被信号到会按默认 disposition
        终止、把 squashfs / overlay mount point 拆掉、user 进程访问内存映射
        时 SIGBUS）。wrapper.py 自己在 SIGTERM handler 里枚举本 job cgroup，
        按 `/proc/<pid>/status` 的 NSpid 字段筛出 user 容器内进程（在子 PID
        namespace 内、且不是容器 PID 1），对它们 `kill(2)` 把信号送进去；
        user-script bash 装的 `trap '' TERM` 让它在 fan-out 时 SIG_IGN 不死，
        SIG_IGN 通过 POSIX exec 继承到 user 进程，user 代码 `signal.signal`
        / `sigaction` 装 handler 自然覆盖。详见 _wrapper_template.py 的
        _signal_user_processes 与 user-script 渲染逻辑。
        """
        command = [
            "scancel",
            f"--signal={signal_name}",
            "--batch",
            slurm_job_id,
        ]

        env: Dict[str, str] = os.environ.copy()
        env["MAGNUS_RUNNER"] = runner
        env["MAGNUS_TOKEN"] = token

        try:
            result = self._transport.run(
                command,
                check = False,
                env = env,
            )
            if result.returncode != 0:
                logger.error(
                    f"scancel --signal={signal_name} failed for job {slurm_job_id} "
                    f"(rc={result.returncode}): {result.stderr.strip()}"
                )
        except Exception as error:
            logger.error(f"scancel --signal={signal_name} failed for job {slurm_job_id}: {error}")
