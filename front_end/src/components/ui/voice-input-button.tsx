// front_end/src/components/ui/voice-input-button.tsx
"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { Mic, Loader2 } from "lucide-react";
import { API_BASE } from "@/lib/config";
import { useLanguage } from "@/context/language-context";


interface VoiceInputButtonProps {
  onTranscript: (text: string) => void;
  context?: string;
  disabled?: boolean;
  className?: string;
}


export function VoiceInputButton({ onTranscript, context, disabled, className }: VoiceInputButtonProps) {
  const { t } = useLanguage();
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // 卸载时释放麦克风
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
        mediaRecorderRef.current.stop();
      }
    };
  }, []);

  const startRecording = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        busyRef.current = false;
        return;
      }

      const mimeType = MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "audio/webm";
      const mediaRecorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());

        const blob = new Blob(chunksRef.current, { type: mimeType });
        if (blob.size === 0 || !mountedRef.current) {
          busyRef.current = false;
          return;
        }

        if (mountedRef.current) setIsTranscribing(true);
        try {
          const formData = new FormData();
          formData.append("file", blob, "recording.mp3"); // 后缀统一 mp3，与后端一致
          formData.append("context", context || "");

          const token = localStorage.getItem("magnus_token");
          const response = await fetch(`${API_BASE}/api/explorer/transcribe`, {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
            body: formData,
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const data = await response.json();
          if (data.text && mountedRef.current) {
            onTranscript(data.text);
          }
        } catch (error) {
          console.error("Transcription error:", error);
        } finally {
          if (mountedRef.current) setIsTranscribing(false);
          busyRef.current = false;
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (error) {
      busyRef.current = false;
      console.error("Microphone error:", error);
    }
  }, [context, onTranscript]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      // busyRef 保持 true，直到 onstop 里转写完成才释放
    }
  }, []);

  const handleClick = useCallback(() => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [isRecording, startRecording, stopRecording]);

  return (
    <button
      onClick={handleClick}
      disabled={disabled || isTranscribing}
      style={{ touchAction: "manipulation" }}
      className={`m-2 p-2 rounded-lg transition-all ${
        isRecording
          ? "bg-red-600 hover:bg-red-500 text-white animate-recording-pulse"
          : isTranscribing
            ? "bg-zinc-700 text-zinc-400 cursor-wait"
            : "bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200"
      } disabled:cursor-not-allowed ${className || ""}`}
      title={
        isRecording
          ? t("explorer.voiceStop")
          : isTranscribing
            ? t("explorer.voiceTranscribing")
            : t("explorer.voiceStart")
      }
    >
      {isTranscribing ? (
        <Loader2 className="w-5 h-5 animate-spin" />
      ) : (
        <Mic className="w-5 h-5" />
      )}
    </button>
  );
}
