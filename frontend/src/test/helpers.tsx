import React, { type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppProvider, AppContext } from "../context/AppContext";
import type { AppState } from "../types";

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
    },
  });
}

interface WrapperOptions {
  initialRoute?: string;
  initialState?: Partial<AppState>;
}

function createWrapper({ initialRoute = "/", initialState }: WrapperOptions = {}) {
  const queryClient = createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <AppProvider>
          <AppContextStateInjector initialState={initialState}>
            <MemoryRouter initialEntries={[initialRoute]}>
              {children}
            </MemoryRouter>
          </AppContextStateInjector>
        </AppProvider>
      </QueryClientProvider>
    );
  };
}

function AppContextStateInjector({
  children,
  initialState,
}: {
  children: ReactNode;
  initialState?: Partial<AppState>;
}) {
  const ctx = React.useContext(AppContext);
  React.useEffect(() => {
    if (ctx && initialState) {
      ctx.dispatch({ type: "SET_STATE", payload: initialState });
    }
  }, [ctx, initialState]);
  return <>{children}</>;
}

export function renderWithProviders(
  ui: React.ReactElement,
  options?: RenderOptions & WrapperOptions,
) {
  const { initialRoute, initialState, ...renderOptions } = options ?? {};
  return render(ui, {
    wrapper: createWrapper({ initialRoute, initialState }),
    ...renderOptions,
  });
}
