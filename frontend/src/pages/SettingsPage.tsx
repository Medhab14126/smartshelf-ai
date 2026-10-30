import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Spinner } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { api } from "@/lib/api";
import { ME_KEY, useAuth } from "@/lib/auth";
import type { Business, NotificationSettings } from "@/lib/types";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function BusinessSettings({ business, canEdit }: { business: Business; canEdit: boolean }) {
  const client = useQueryClient();
  const [values, setValues] = useState(business);
  const timezones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [business.timezone];
  const save = useMutation({
    mutationFn: () => api.put<Business>("/api/settings/business", values as unknown as Record<string, unknown>),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ME_KEY });
      client.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Business settings saved");
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Business</CardTitle>
        <CardDescription>The timezone decides when a product expires “today” and when reminders are sent.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="Business name" htmlFor="b_name" required>
            <Input id="b_name" value={values.name} disabled={!canEdit} onChange={(e) => setValues({ ...values, name: e.target.value })} required />
          </Field>
          <Field label="Timezone" htmlFor="b_tz" required>
            <Select id="b_tz" value={values.timezone} disabled={!canEdit} onChange={(e) => setValues({ ...values, timezone: e.target.value })}>
              {[...new Set([values.timezone, ...timezones])].map((tz) => (
                <option key={tz}>{tz}</option>
              ))}
            </Select>
          </Field>
          <Field label="Default currency" htmlFor="b_cur" required>
            <Input id="b_cur" maxLength={3} value={values.currency} disabled={!canEdit} onChange={(e) => setValues({ ...values, currency: e.target.value.toUpperCase() })} />
          </Field>
          <Field label="Numeric date order on packaging" htmlFor="b_df" required hint="Used to read dates like 03/04/2026. Ambiguous dates are always flagged for review.">
            <Select id="b_df" value={values.date_format} disabled={!canEdit} onChange={(e) => setValues({ ...values, date_format: e.target.value as "DMY" | "MDY" })}>
              <option value="DMY">Day / Month / Year (03/04 = 3 April)</option>
              <option value="MDY">Month / Day / Year (03/04 = March 4)</option>
            </Select>
          </Field>
          {canEdit && (
            <Button type="submit" className="sm:col-span-2 sm:w-fit" loading={save.isPending}>
              Save business settings
            </Button>
          )}
        </form>
      </CardContent>
    </Card>
  );
}

function NotificationSettingsCard({ initial, canEdit }: { initial: NotificationSettings; canEdit: boolean }) {
  const client = useQueryClient();
  const [values, setValues] = useState(initial);
  const [newEmail, setNewEmail] = useState("");
  const [newDay, setNewDay] = useState("");
  useEffect(() => setValues(initial), [initial]);
  const save = useMutation({
    mutationFn: () =>
      api.put<NotificationSettings>("/api/settings/notifications", {
        enabled: values.enabled,
        recipient_emails: values.recipient_emails,
        reminder_days: values.reminder_days,
        notify_on_expired: values.notify_on_expired,
        send_hour_local: values.send_hour_local,
      }),
    onSuccess: (saved) => {
      client.setQueryData(["settings", "notifications"], saved);
      client.invalidateQueries({ queryKey: ["dashboard"] });
      toast.success("Notification settings saved");
    },
    onError: (error) => toast.error(error.message),
  });
  const addEmail = () => {
    const email = newEmail.trim().toLowerCase();
    if (!EMAIL_RE.test(email)) return toast.error("Enter a valid email address");
    if (!values.recipient_emails.includes(email)) setValues({ ...values, recipient_emails: [...values.recipient_emails, email] });
    setNewEmail("");
  };
  const addDay = () => {
    const day = Number(newDay);
    if (!Number.isInteger(day) || day < 0 || day > 365) return toast.error("Enter a whole number of days between 0 and 365");
    setValues({ ...values, reminder_days: [...new Set([...values.reminder_days, day])].sort((a, b) => b - a) });
    setNewDay("");
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Expiry reminders</CardTitle>
        <CardDescription>Emails are sent only for batches with confirmed expiry dates that are still in stock.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {!values.smtp_configured && (
          <Alert tone="warning" title="Email server not configured">
            The server has no SMTP settings, so reminders will be recorded as failed until an administrator configures SMTP_HOST.
          </Alert>
        )}
        <label className="flex items-center gap-2 text-sm font-medium">
          <input type="checkbox" className="size-4" checked={values.enabled} disabled={!canEdit} onChange={(e) => setValues({ ...values, enabled: e.target.checked })} />
          Send expiry reminder emails
        </label>

        <div>
          <p className="mb-2 text-sm font-medium">Recipients</p>
          <ul className="mb-2 flex flex-wrap gap-2">
            {values.recipient_emails.map((email) => (
              <li key={email} className="flex items-center gap-1 rounded-full border bg-muted/50 py-1 pl-3 pr-1 text-sm">
                {email}
                {canEdit && (
                  <button
                    type="button"
                    className="rounded-full p-1 hover:bg-accent"
                    aria-label={`Remove ${email}`}
                    onClick={() => setValues({ ...values, recipient_emails: values.recipient_emails.filter((e) => e !== email) })}
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </li>
            ))}
            {!values.recipient_emails.length && <li className="text-sm text-muted-foreground">No recipients — nobody will be emailed.</li>}
          </ul>
          {canEdit && (
            <div className="flex max-w-md gap-2">
              <Input
                aria-label="Recipient email"
                type="email"
                placeholder="team@yourcafe.com"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addEmail())}
              />
              <Button type="button" variant="outline" onClick={addEmail}>
                <Plus /> Add
              </Button>
            </div>
          )}
        </div>

        <div>
          <p className="mb-2 text-sm font-medium">Remind me this many days before expiry</p>
          <ul className="mb-2 flex flex-wrap gap-2">
            {values.reminder_days.map((day) => (
              <li key={day} className="flex items-center gap-1 rounded-full border bg-muted/50 py-1 pl-3 pr-1 text-sm">
                {day === 0 ? "On the day" : `${day} day${day === 1 ? "" : "s"}`}
                {canEdit && values.reminder_days.length > 1 && (
                  <button
                    type="button"
                    className="rounded-full p-1 hover:bg-accent"
                    aria-label={`Remove ${day} day reminder`}
                    onClick={() => setValues({ ...values, reminder_days: values.reminder_days.filter((d) => d !== day) })}
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {canEdit && (
            <div className="flex max-w-xs gap-2">
              <Input aria-label="Days before expiry" type="number" min={0} max={365} value={newDay} onChange={(e) => setNewDay(e.target.value)} />
              <Button type="button" variant="outline" onClick={addDay}>
                <Plus /> Add
              </Button>
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={values.notify_on_expired}
            disabled={!canEdit}
            onChange={(e) => setValues({ ...values, notify_on_expired: e.target.checked })}
          />
          Also send one alert when a batch has expired but is still in stock
        </label>

        <Field label="Earliest local hour to send emails" htmlFor="n_hour" className="max-w-xs" required>
          <Select id="n_hour" value={values.send_hour_local} disabled={!canEdit} onChange={(e) => setValues({ ...values, send_hour_local: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, "0")}:00
              </option>
            ))}
          </Select>
        </Field>
        {canEdit && (
          <Button onClick={() => save.mutate()} loading={save.isPending}>
            Save reminder settings
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

export function SettingsPage() {
  const { me } = useAuth();
  const notifications = useQuery({
    queryKey: ["settings", "notifications"],
    queryFn: () => api.get<NotificationSettings>("/api/settings/notifications"),
  });
  if (!me) return null;
  const canEdit = me.role === "owner";
  return (
    <>
      <PageHeader title="Settings" description={canEdit ? undefined : "Only the business owner can change settings."} />
      <div className="max-w-4xl space-y-4">
        <BusinessSettings business={me.business} canEdit={canEdit} />
        {notifications.data ? <NotificationSettingsCard initial={notifications.data} canEdit={canEdit} /> : <Spinner />}
      </div>
    </>
  );
}
