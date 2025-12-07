from library import *

def perform_bandwidth_test(src_device, dst_device, size_mb=1024):
    """
    在两个设备间传输数据，计算带宽
    """
    # 准备数据: 1GB (1024 * 1024 * 1024 bytes)
    # float32 占 4 bytes，所以元素个数 = 总字节 / 4
    num_elements = (size_mb * 1024 * 1024) // 4
    x = torch.randn(num_elements, device=src_device, dtype=torch.float32)
    
    # 预热 (Warmup) - 让 GPU 从休眠中醒来，频率拉满
    # 这一步非常重要，否则第一次传输会因为驱动初始化而很慢
    for _ in range(5):
        _ = x.to(dst_device)
    torch.cuda.synchronize()

    # 正式测试
    start_time = time.time()
    iterations = 10
    
    for _ in range(iterations):
        _ = x.to(dst_device)
    
    # 等待传输完成
    torch.cuda.synchronize()
    end_time = time.time()

    # 计算带宽
    total_bytes = size_mb * 1024 * 1024 * iterations
    duration = end_time - start_time
    bandwidth = (total_bytes / duration) / (1024**3) # GB/s

    return bandwidth

def main():
    print(f"🚀 [Magnus] GPU 互联检测脚本启动")
    print(f"PyTorch Version: {torch.__version__}")
    
    if not torch.cuda.is_available():
        print("❌ 悲报：没有检测到 CUDA 环境")
        return

    n_devices = torch.cuda.device_count()
    print(f"检测到 GPU 数量: {n_devices}")
    
    if n_devices < 2:
        print("❌ 只有一张卡，无法测试互联。请在 sbatch 中申请 --gres=gpu:2")
        return

    # 打印显卡名字
    for i in range(n_devices):
        print(f"GPU {i}: {torch.cuda.get_device_name(i)}")

    print("-" * 50)

    # 1. 询问 PyTorch 官方说法 (can_device_access_peer)
    print("🔍 阶段一：P2P 权限检查 (Software Capability)")
    p2p_0_to_1 = torch.cuda.can_device_access_peer(0, 1)
    p2p_1_to_0 = torch.cuda.can_device_access_peer(1, 0)

    print(f"GPU 0 -> GPU 1 P2P Access: {'✅ 允许' if p2p_0_to_1 else '❌ 禁止 (需经过 CPU)'}")
    print(f"GPU 1 -> GPU 0 P2P Access: {'✅ 允许' if p2p_1_to_0 else '❌ 禁止 (需经过 CPU)'}")
    
    if not p2p_0_to_1:
        print("\n⚠️ 警告：P2P 未开启，这意味着数据必须先拷贝到 CPU 内存，再拷贝到另一张卡。效率会很低。")

    print("-" * 50)

    # 2. 实测带宽
    print("🏎️ 阶段二：带宽压力测试 (Hardware Reality)")
    print("正在两张卡之间搬运 10GB 数据，请稍候...")
    
    # 0 -> 1
    speed_0_1 = perform_bandwidth_test(torch.device("cuda:0"), torch.device("cuda:1"))
    print(f"带宽 (GPU 0 -> GPU 1): {speed_0_1:.2f} GB/s")
    
    # 1 -> 0
    speed_1_0 = perform_bandwidth_test(torch.device("cuda:1"), torch.device("cuda:0"))
    print(f"带宽 (GPU 1 -> GPU 0): {speed_1_0:.2f} GB/s")

    # 3. 最终判决
    print("-" * 50)
    print("⚖️ [Magnus 判决书]")
    avg_speed = (speed_0_1 + speed_1_0) / 2
    
    if avg_speed > 300:
        print("💎 恭喜！这是真的 NVLink！(这不科学，如果是 5090 的话)")
    elif avg_speed > 45:
        print("🥇 优秀！这是 PCIe P2P 直连 (走 PCIe Switch)，无 NVLink 的最佳状态。")
    elif avg_speed > 20:
        print("🥈 普通。这是标准 PCIe 5.0 (可能经过了 CPU Root Complex)，能用。")
    else:
        print("🥉 较差。数据在 CPU 内存里绕了一大圈 (System Memory Fallback)。分布式训练建议仅用 DDP。")

if __name__ == "__main__":
    
    main()