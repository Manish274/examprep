import { loadDotEnv } from "./dotenv.js";

/**
 * Side-effect module. Entrypoints import this FIRST so the repo-root .env is
 * in process.env before any other module reads it at import time — module
 * evaluation follows import order, so a plain function call placed among the
 * imports would run too late.
 */
loadDotEnv();
