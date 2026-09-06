import { zValidator } from "@hono/zod-validator";
import type { ValidationTargets } from "hono";
import type { ZodSchema } from "zod";
import { unprocessable } from "./errors.js";

/**
 * zValidator with the error shape the rest of the API uses.
 *
 * Left to itself the validator writes a raw ZodError straight to the response,
 * so a client would face two different error formats depending on where the
 * request failed. Throwing instead routes it through the single error handler.
 */
export const validate = <T extends ZodSchema, Target extends keyof ValidationTargets>(
  target: Target,
  schema: T,
) =>
  zValidator(target, schema, (result) => {
    if (!result.success) {
      throw unprocessable(
        "Request failed validation",
        result.error.issues.map((issue) => ({
          path: issue.path.join("."),
          message: issue.message,
          code: issue.code,
        })),
      );
    }
    return undefined;
  });
