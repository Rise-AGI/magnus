// front_end/src/app/login/page.tsx
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/context/auth-context";
import { AuthForm } from "@/components/auth/auth-form";
import { DEFAULT_ROUTE } from "@/lib/config";


export default function LoginPage() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user) {
      router.push(DEFAULT_ROUTE);
    }
  }, [user, router]);

  if (user) return null;

  return (
    <div className="min-h-dvh w-full bg-[#050505] flex items-center justify-center px-4">
      <AuthForm />
    </div>
  );
}
