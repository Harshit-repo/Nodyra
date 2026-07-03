import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, getToken, getUser } from "./api";
import { LoginPage } from "./LoginPage";
import type { UserInfo } from "./types";

const USER: UserInfo = {
  id: "u1",
  email: "a@b.com",
  name: "Ada",
  company: "Acme",
  role: "owner",
};

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("LoginPage — FE-1 cookie session (no localStorage token)", () => {
  it("signs in without persisting the bearer token (httpOnly cookie carries the session)", async () => {
    vi.spyOn(api, "login").mockResolvedValue({ token: "secret-bearer", user: USER });
    const onSignedIn = vi.fn();
    render(<LoginPage registrationOpen={false} onSignedIn={onSignedIn} />);

    fireEvent.change(screen.getByPlaceholderText(/you@example\.com/i), {
      target: { value: "a@b.com" },
    });
    fireEvent.change(screen.getByLabelText(/password/i), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(USER));

    // The session token must NEVER land in localStorage — an XSS could read it
    // there. Authentication rides the httpOnly nodyra_session cookie instead.
    expect(localStorage.getItem("nodyra_token")).toBeNull();
    expect(getToken()).toBeNull();
    // The non-secret user profile is kept for UI state.
    expect(getUser()).toEqual(USER);
  });
});

describe("token key migration", () => {
  it("adopts a legacy noodle_token and removes it", () => {
    localStorage.setItem("noodle_token", "tok-123");

    expect(getToken()).toBe("tok-123");
    expect(localStorage.getItem("nodyra_token")).toBe("tok-123");
    expect(localStorage.getItem("noodle_token")).toBeNull();
  });

  it("prefers the new key when both exist", () => {
    localStorage.setItem("nodyra_token", "new");
    localStorage.setItem("noodle_token", "old");

    expect(getToken()).toBe("new");
  });
});
