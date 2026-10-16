import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { safeRedirect } from "@/lib/safeRedirect";
import { publicOrigin } from "@/lib/publicOrigin";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  // Use the public origin, not request.url's origin — behind Railway's proxy the latter is
  // http://localhost:8080, which would bounce the just-signed-in user to a dead address.
  const origin = publicOrigin(request);
  const code = searchParams.get("code");
  const next = safeRedirect(searchParams.get("redirect"));

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }
  return NextResponse.redirect(`${origin}/login?error=oauth`);
}
