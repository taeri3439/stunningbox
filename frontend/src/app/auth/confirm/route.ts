import { type EmailOtpType } from "@supabase/supabase-js";
import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { safeRedirect } from "@/lib/safeRedirect";
import { publicOrigin } from "@/lib/publicOrigin";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  // See publicOrigin: request.url's origin is the internal proxy host on Railway.
  const origin = publicOrigin(request);
  const token_hash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;
  // recovery links carry next=/update-password; signup confirmation defaults to /app.
  const next = safeRedirect(searchParams.get("next"));

  if (token_hash && type) {
    const supabase = await createClient();
    const { error } = await supabase.auth.verifyOtp({ type, token_hash });
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }
  return NextResponse.redirect(`${origin}/login?error=confirm`);
}
