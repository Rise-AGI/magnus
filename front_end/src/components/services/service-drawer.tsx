// front_end/src/components/services/service-drawer.tsx
"use client";

import { useRef } from "react";
import { Server, RefreshCw } from "lucide-react";

import { Drawer } from "@/components/ui/drawer";
import { Service } from "@/types/service";
import ServiceForm, { ServiceFormData } from "./service-form";
import { ConfigClipboard } from "@/components/ui/config-clipboard";
import { HelpButton } from "@/components/ui/help-button";
import { ServiceFormHelp } from "@/components/ui/help-content";
import { useLanguage } from "@/context/language-context";


interface ServiceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  initialData?: Service | ServiceFormData | null;
  onSuccess: () => void;
}


export function ServiceDrawer({
  isOpen,
  onClose,
  initialData,
  onSuccess,
}: ServiceDrawerProps): JSX.Element {

  const { t } = useLanguage();
  const formRef = useRef<any>(null);

  const isEdit = !!initialData;
  const title = isEdit ? t("serviceForm.cloneUpdate") : t("serviceForm.create");

  return (
    <Drawer
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      icon={isEdit ? <RefreshCw className="w-5 h-5 text-purple-500" /> : <Server className="w-5 h-5 text-blue-500" />}
      width="w-[650px]"
      actions={
        <>
          <HelpButton title={t("serviceForm.help")}>
            <ServiceFormHelp />
          </HelpButton>
          <ConfigClipboard
            kind="magnus/service"
            onGetPayload={() => formRef.current?.getPayload()}
            onApplyPayload={(p) => formRef.current?.applyPayload(p)}
          />
        </>
      }
    >
      {isOpen && (
        <ServiceForm
          ref={formRef}
          initialData={initialData}
          onCancel={onClose}
          onSuccess={onSuccess}
        />
      )}
    </Drawer>
  );
}