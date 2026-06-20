import { Suspense } from "react";
import { Outlet } from "react-router-dom";

import { BackendLoading } from "./BackendLoading";
import { HomeHeader } from "./HomeHeader";

export function HomeLayout() {
  return (
    <>
      <HomeHeader />
      <Suspense fallback={<BackendLoading retrying={false} />}>
        <Outlet />
      </Suspense>
    </>
  );
}
