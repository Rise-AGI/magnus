// front_end/src/components/jobs/job-drawer.tsx
import { useRef } from "react";
import { Rocket, RefreshCw } from "lucide-react";
import JobForm, { JobFormData } from "@/components/jobs/job-form";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard";
import { HelpButton } from "@/components/ui/help-button";
import { JobFormHelp } from "@/components/ui/help-content";
import { useLanguage } from "@/context/language-context";

interface JobDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  mode: "create" | "clone";
  initialData: JobFormData | null;
  formKey?: string;
}

export function JobDrawer({
  isOpen,
  onClose,
  onSuccess,
  mode,
  initialData,
  formKey
}: JobDrawerProps) {

  const { t } = useLanguage();
  const formRef = useRef<any>(null);

  const title = mode === 'create' ? t("jobs.submitNewJob") : t("jobs.cloneJob");
  const icon = mode === 'create' ? <Rocket className="w-5 h-5 text-blue-500"/> : <RefreshCw className="w-5 h-5 text-purple-500"/>;
  const desc = undefined;

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      icon={icon}
      description={desc}
      width="w-[650px]"
      actions={
        <>
          <HelpButton title={t("jobs.submitHelp")}>
            <JobFormHelp />
          </HelpButton>
          <ConfigClipboard
            kind="magnus/job"
            onGetPayload={() => formRef.current?.getPayload()}
            onApplyPayload={(p) => formRef.current?.applyPayload(p)}
          />
        </>
      }
    >
      <JobForm
        ref={formRef}
        key={formKey || mode}
        mode={mode}
        initialData={initialData}
        onCancel={onClose}
        onSuccess={onSuccess}
      />
    </Drawer>
  );
}