"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Terminal, Loader2, Play } from "lucide-react";
import { client } from "@/lib/api";
import { Drawer } from "@/components/ui/drawer";
import { DynamicForm } from "@/components/ui/dynamic-form";
import { FieldSchema, getFieldInitialValue, validateFieldValue } from "@/components/ui/dynamic-form/types";
import { BlueprintPreference } from "@/types/blueprint";
import { computeStableHash } from "@/lib/utils";
import { HelpButton } from "@/components/ui/help-button";
import { BlueprintRunnerHelp } from "@/components/ui/help-content";
import { useLanguage } from "@/context/language-context";

interface BlueprintRunnerProps {
  blueprint: { id: string; title: string } | null;
  onClose: () => void;
}

export function BlueprintRunner({ blueprint, onClose }: BlueprintRunnerProps) {
  const { t } = useLanguage();
  const router = useRouter();

  const [paramsSchema, setParamsSchema] = useState<FieldSchema[]>([]);
  const [formValues, setFormValues] = useState<Record<string, any>>({});

  const [isLoadingSchema, setIsLoadingSchema] = useState(false);
  const [isRunning, setIsRunning] = useState(false);

  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const currentHashRef = useRef<string>("");

  useEffect(() => {
    if (blueprint?.id) {
      let isMounted = true;

      const initialize = async () => {
        setIsLoadingSchema(true);
        setParamsSchema([]);
        setErrorField(null);
        setErrorMessage(null);
        setFormValues({});
        currentHashRef.current = "";

        try {
          const results = await Promise.allSettled([
            client(`/api/blueprints/${blueprint.id}/schema`),
            client(`/api/blueprints/${blueprint.id}/preference`, { cache: "no-store" })
          ]);

          if (!isMounted) return;
          const schemaResult = results[0];
          if (schemaResult.status === "rejected") {
            throw new Error(schemaResult.reason.message || "Failed to fetch schema");
          }
          const schema = schemaResult.value;
          if (!Array.isArray(schema)) {
            throw new Error("Invalid schema format");
          }

          // 处理 Preference 时允许 404
          let preference: BlueprintPreference | null = null;
          const prefResult = results[1];
          if (prefResult.status === "fulfilled") {
            preference = prefResult.value as BlueprintPreference;
          }

          const signatureHash = await computeStableHash(schema);
          currentHashRef.current = signatureHash;
          setParamsSchema(schema);
          
          const initial: Record<string, any> = {};
          const useCache = preference && preference.blueprint_hash === signatureHash;
          const cached = useCache ? preference!.cached_params : {};

          schema.forEach((p: FieldSchema) => {
            initial[p.key] = getFieldInitialValue(p, cached[p.key]);
          });

          setFormValues(initial);

        } catch (e: any) {
          if (isMounted) {
            console.error("Blueprint initialization failed:", e);
            setErrorMessage(t("blueprintRunner.loadFailed") + `: ${e.message}`);
          }
        } finally {
          if (isMounted) setIsLoadingSchema(false);
        }
      };

      initialize();
      return () => { isMounted = false; };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blueprint?.id]);

  const scrollToError = (key: string) => {
    setTimeout(() => {
      const el = document.getElementById(`field-${key}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 100);
  };

  const handleRun = async () => {
    if (!blueprint) return;

    setErrorField(null);
    setErrorMessage(null);

    for (const param of paramsSchema) {
      const err = validateFieldValue(param, formValues[param.key]);
      if (err) {
        setErrorField(param.key);
        setErrorMessage(`⚠️ ${err}`);
        scrollToError(param.key);
        return;
      }
    }

    setIsRunning(true);
    try {
      await client(`/api/blueprints/${blueprint.id}/run`, {
        method: "POST",
        json: {
          parameters: formValues,
          use_preference: false,  // 前端已合并缓存，不需要后端再合并
          save_preference: true,  // 统一由后端保存偏好
        }
      });

      sessionStorage.setItem('magnus_new_job', 'true');
      router.refresh();
      router.push('/jobs');
    } catch (e: any) {
      setErrorMessage(`Error: ${e.message}`);
      setIsRunning(false);
    }
  };

  const handleFieldChange = (key: string, val: any) => {
    setFormValues(prev => ({ ...prev, [key]: val }));
    if (errorField === key) {
      setErrorField(null);
      setErrorMessage(null);
    }
  };

  return (
    <Drawer
      isOpen={!!blueprint}
      onClose={onClose}
      title={blueprint?.title}
      icon={<Terminal className="w-5 h-5 text-blue-500" />}
      width="w-[600px]"
      actions={
        <HelpButton title={t("blueprintRunner.help")}>
          <BlueprintRunnerHelp />
        </HelpButton>
      }
    >
      <div className="flex flex-col min-h-full">
        <div className="flex-1 pb-4">
          <DynamicForm
            schema={paramsSchema}
            values={formValues}
            onChange={handleFieldChange}
            isLoading={isLoadingSchema}
            errorField={errorField}
          />
        </div>

        <div className="mt-auto pt-6 border-t border-zinc-800 flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-4 pb-1">
          {errorMessage ? (
            <span className="text-red-500 text-xs font-bold animate-pulse text-center sm:text-left transition-all">
              {errorMessage}
            </span>
          ) : (
            <span className="text-zinc-500 text-xs text-center sm:text-left hidden sm:block transition-all">
              {t("jobForm.waitingForLaunch")}
            </span>
          )}

          <div className="flex gap-3 w-full sm:w-auto">
            <button
              onClick={onClose}
              disabled={isRunning}
              className="flex-1 sm:flex-none px-4 py-2.5 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
            >
              {t("common.cancel")}
            </button>
            <button
              onClick={handleRun}
              disabled={isRunning || isLoadingSchema}
              className="flex-1 sm:flex-none px-6 py-2.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 active:scale-95 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isRunning ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t("blueprintRunner.launching")}
                </>
              ) : (
                <>
                  <Play className="w-4 h-4 fill-current" />
                  {t("blueprintRunner.launch")}
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </Drawer>
  );
}