/** Values attached to the Hono context by middleware. */
export interface AppVariables {
  requestId: string;
  userId?: string;
  loginSessionId?: string;
}

export type AppEnv = { Variables: AppVariables };
