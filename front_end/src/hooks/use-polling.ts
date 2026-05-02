import { useEffect, useRef } from "react";

/**
 * 后台轮询：以固定间隔调用 callback，callback 始终是最新闭包。
 * 初始 fetch 与依赖变化时的重取由调用方自管，不与轮询周期耦合。
 */
export function usePolling(callback: () => void, intervalMs: number) {
  const callbackRef = useRef(callback);
  useEffect(() => { callbackRef.current = callback; }, [callback]);
  useEffect(() => {
    const id = setInterval(() => callbackRef.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}
