import { Router } from "express"

export const router = Router()

export function renderHome(): string {
  return "ok"
}

router.get("/ui-health", () => "ok")
