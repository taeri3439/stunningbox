import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim();
  const devBypass =
    process.env.DEV_NO_AUTH === "1" ||
    (process.env.NODE_ENV === "development" && (!supabaseUrl || !supabaseKey));

  if (devBypass) {
    const path = request.nextUrl.pathname;
    if (path === "/login" || path === "/signup") {
      const url = request.nextUrl.clone();
      url.pathname = "/app";
      url.search = "";
      return NextResponse.redirect(url);
    }
    return NextResponse.next({ request });
  }

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  // IMPORTANT: call getUser() immediately, with no code between it and createServerClient.
  // getUser() revalidates the JWT with Supabase (getSession() only reads the cookie).
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const path = request.nextUrl.pathname;
  const isProtected =
    path === "/app" ||
    path.startsWith("/app/") ||
    path.startsWith("/p/") ||
    path.startsWith("/c/") ||
    path === "/update-password";
  const isAuthPage = path === "/login" || path === "/signup";

  if (!user && isProtected) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    url.searchParams.set("redirect", path + request.nextUrl.search);
    const redirectRes = NextResponse.redirect(url);
    response.cookies.getAll().forEach((c) => redirectRes.cookies.set(c));
    return redirectRes;
  }

  if (user && isAuthPage) {
    const url = request.nextUrl.clone();
    url.pathname = "/app";
    url.search = "";
    const redirectRes = NextResponse.redirect(url);
    response.cookies.getAll().forEach((c) => redirectRes.cookies.set(c));
    return redirectRes;
  }

  return response;
}

export const config = {
  matcher: [
    // Exclude /api/* (the FastAPI proxy — not gated here) so we don't run getUser()
    // on every API call (the processing screen polls status ~1/s).
    "/((?!_next/static|_next/image|favicon.ico|icon.svg|api/|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
