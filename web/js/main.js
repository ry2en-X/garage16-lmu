// main.js — the website's entry point (index.html loads only this): starts the router
// (core/router.js), which draws the page for the address and loads every page module on demand.
import { startRouter } from "./core/router.js";

startRouter();
