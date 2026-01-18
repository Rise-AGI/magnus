// front_end/src/components/jobs/job-drawer.tsx
import { useRef } from "react";
import { Rocket, RefreshCw } from "lucide-react";
import JobForm, { JobFormData } from "@/components/jobs/job-form";
import { Drawer } from "@/components/ui/drawer";
import { ConfigClipboard } from "@/components/ui/config-clipboard"

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

  const formRef = useRef<any>(null);
  
  const title = mode === 'create' ? "Submit New Job" : "Clone Job";
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
        <ConfigClipboard 
          kind="magnus/job"
          onGetPayload={() => formRef.current?.getPayload()}
          onApplyPayload={(p) => formRef.current?.applyPayload(p)}
        />
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