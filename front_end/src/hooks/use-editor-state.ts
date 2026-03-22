// front_end/src/hooks/use-editor-state.ts
import { useState, useEffect, useRef, useCallback, Dispatch, SetStateAction } from "react";

interface ValidationError {
  field: string;
  message: string;
  scrollTo?: string;
}

interface UseEditorStateOptions<T> {
  isOpen: boolean;
  initialData: T;
  onSave: (data: T) => Promise<void>;
  onClose: () => void;
  validate: (data: T) => ValidationError | null;
  labels?: {
    discardTitle?: string;
    discardConfirm?: string;
    discardBtn?: string;
    saveFailed?: string;
  };
}

interface UseEditorStateReturn<T> {
  formData: T;
  setFormData: Dispatch<SetStateAction<T>>;
  isSaving: boolean;
  isDirty: boolean;
  errorField: string | null;
  errorMessage: string | null;
  clearError: (field: string) => void;
  scrollToError: (id: string) => void;
  showSaveToast: boolean;
  toastFading: boolean;
  handleButtonSave: () => void;
  guardedClose: () => void;
  discardDialogProps: {
    isOpen: boolean;
    onClose: () => void;
    onConfirm: () => void;
    title: string;
    description: string;
    confirmText: string;
    variant: "danger";
  };
}

export function useEditorState<T>({
  isOpen,
  initialData,
  onSave,
  onClose,
  validate,
  labels,
}: UseEditorStateOptions<T>): UseEditorStateReturn<T> {
  const [formData, setFormData] = useState<T>(initialData);
  const [isSaving, setIsSaving] = useState(false);
  const [errorField, setErrorField] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [showSaveToast, setShowSaveToast] = useState(false);
  const [toastFading, setToastFading] = useState(false);
  const [showDiscardDialog, setShowDiscardDialog] = useState(false);

  const keepOpenRef = useRef(false);
  const handleSubmitRef = useRef<() => void>(() => {});
  const fadeTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const hideTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const snapshotRef = useRef<string>("");

  // Reset all state on open
  useEffect(() => {
    if (isOpen) {
      setFormData(initialData);
      snapshotRef.current = JSON.stringify(initialData);
      setIsSaving(false);
      setErrorField(null);
      setErrorMessage(null);
      setShowSaveToast(false);
      setToastFading(false);
      setShowDiscardDialog(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only reset on open toggle
  }, [isOpen]);

  const isDirty = isOpen && JSON.stringify(formData) !== snapshotRef.current;

  // Ctrl+S / Cmd+S
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        keepOpenRef.current = true;
        handleSubmitRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen]);

  // beforeunload guard
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  const scrollToError = useCallback((id: string) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const clearError = useCallback((field: string) => {
    setErrorField(prev => {
      if (prev !== field) return prev;
      // Defer setErrorMessage to avoid nested state updates
      queueMicrotask(() => setErrorMessage(null));
      return null;
    });
  }, []);

  const handleSubmit = useCallback(async () => {
    if (isSaving) return;

    setErrorField(null);
    setErrorMessage(null);

    const validationError = validate(formData);
    if (validationError) {
      setErrorField(validationError.field);
      setErrorMessage(`⚠️ ${validationError.message}`);
      if (validationError.scrollTo) scrollToError(validationError.scrollTo);
      keepOpenRef.current = false;
      return;
    }

    setIsSaving(true);
    try {
      await onSave(formData);
      // Update snapshot so isDirty resets after save
      snapshotRef.current = JSON.stringify(formData);
      if (keepOpenRef.current) {
        clearTimeout(fadeTimerRef.current);
        clearTimeout(hideTimerRef.current);
        setShowSaveToast(true);
        setToastFading(false);
        fadeTimerRef.current = setTimeout(() => setToastFading(true), 1500);
        hideTimerRef.current = setTimeout(() => {
          setShowSaveToast(false);
          setToastFading(false);
        }, 2000);
      } else {
        onClose();
      }
    } catch (e: any) {
      setErrorField("_submit");
      setErrorMessage(`⚠️ ${e.message || labels?.saveFailed || "Save failed"}`);
    } finally {
      keepOpenRef.current = false;
      setIsSaving(false);
    }
  }, [formData, isSaving, onSave, onClose, validate, scrollToError, labels]);

  handleSubmitRef.current = handleSubmit;

  const handleButtonSave = useCallback(() => {
    keepOpenRef.current = false;
    handleSubmitRef.current();
  }, []);

  const guardedClose = useCallback(() => {
    if (isDirty) {
      setShowDiscardDialog(true);
      return;
    }
    onClose();
  }, [isDirty, onClose]);

  const discardDialogProps = {
    isOpen: showDiscardDialog,
    onClose: () => setShowDiscardDialog(false),
    onConfirm: () => { setShowDiscardDialog(false); onClose(); },
    title: labels?.discardTitle || "Unsaved Changes",
    description: labels?.discardConfirm || "Discard unsaved changes?",
    confirmText: labels?.discardBtn || "Discard",
    variant: "danger" as const,
  };

  // Clean up timers
  useEffect(() => {
    return () => {
      clearTimeout(fadeTimerRef.current);
      clearTimeout(hideTimerRef.current);
    };
  }, []);

  return {
    formData,
    setFormData,
    isSaving,
    isDirty,
    errorField,
    errorMessage,
    clearError,
    scrollToError,
    showSaveToast,
    toastFading,
    handleButtonSave,
    guardedClose,
    discardDialogProps,
  };
}
