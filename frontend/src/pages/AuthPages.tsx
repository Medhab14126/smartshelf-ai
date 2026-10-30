import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/feedback";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api } from "@/lib/api";
import { ME_KEY } from "@/lib/auth";
import type { Me } from "@/lib/types";

function AuthShell({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-emerald-50 to-background px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center justify-center gap-2">
          <img src="/favicon.svg" alt="" className="size-10" />
          <span className="text-xl font-semibold">SmartShelf AI</span>
        </div>
        <Card>
          <CardHeader>
            <CardTitle className="text-xl">{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent>{children}</CardContent>
        </Card>
      </div>
    </div>
  );
}

const loginSchema = z.object({
  email: z.string().trim().email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
type LoginValues = z.infer<typeof loginSchema>;

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<LoginValues>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });

  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const me = await api.post<Me>("/api/auth/login", values);
      client.setQueryData(ME_KEY, { user: me.user, business: me.business, role: me.role });
      toast.success(`Welcome back, ${me.user.full_name.split(" ")[0]}`);
      navigate((location.state as { from?: string } | null)?.from ?? "/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    }
  });

  const { errors, isSubmitting } = form.formState;
  return (
    <AuthShell title="Sign in" description="Track deliveries, stock and expiry dates for your cafe.">
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {error && <Alert tone="error">{error}</Alert>}
        <Field label="Email" htmlFor="email" error={errors.email?.message} required>
          <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" error={errors.password?.message} required>
          <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!errors.password} {...form.register("password")} />
        </Field>
        <Button type="submit" className="w-full" loading={isSubmitting}>
          Sign in
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          New to SmartShelf?{" "}
          <Link to="/register" className="font-medium text-primary underline-offset-4 hover:underline">
            Create an account
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}

const registerSchema = z.object({
  full_name: z.string().trim().min(1, "Enter your name"),
  business_name: z.string().trim().min(1, "Enter your cafe or business name"),
  email: z.string().trim().email("Enter a valid email address"),
  password: z
    .string()
    .min(8, "Use at least 8 characters")
    .refine((v) => /[A-Za-z]/.test(v) && /[^A-Za-z]/.test(v), "Mix letters with numbers or symbols"),
  timezone: z.string().min(1),
});
type RegisterValues = z.infer<typeof registerSchema>;

export function RegisterPage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      full_name: "",
      business_name: "",
      email: "",
      password: "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    },
  });
  const onSubmit = form.handleSubmit(async (values) => {
    setError(null);
    try {
      const me = await api.post<Me>("/api/auth/register", values);
      client.setQueryData(ME_KEY, { user: me.user, business: me.business, role: me.role });
      toast.success("Your workspace is ready");
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    }
  });
  const { errors, isSubmitting } = form.formState;
  return (
    <AuthShell title="Create your workspace" description="One workspace per cafe. You can invite your team later.">
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {error && <Alert tone="error">{error}</Alert>}
        <Field label="Your name" htmlFor="full_name" error={errors.full_name?.message} required>
          <Input id="full_name" autoComplete="name" aria-invalid={!!errors.full_name} {...form.register("full_name")} />
        </Field>
        <Field label="Cafe / business name" htmlFor="business_name" error={errors.business_name?.message} required>
          <Input id="business_name" aria-invalid={!!errors.business_name} {...form.register("business_name")} />
        </Field>
        <Field label="Email" htmlFor="email" error={errors.email?.message} required>
          <Input id="email" type="email" autoComplete="email" aria-invalid={!!errors.email} {...form.register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" required error={errors.password?.message} hint="At least 8 characters, letters and numbers">
          <Input id="password" type="password" autoComplete="new-password" aria-invalid={!!errors.password} {...form.register("password")} />
        </Field>
        <Field label="Timezone" htmlFor="timezone" required hint="Used to decide when a product expires 'today'">
          <Input id="timezone" {...form.register("timezone")} />
        </Field>
        <Button type="submit" className="w-full" loading={isSubmitting}>
          Create account
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-primary underline-offset-4 hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
