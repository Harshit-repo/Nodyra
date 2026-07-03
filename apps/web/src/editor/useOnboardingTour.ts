import { useState, useCallback, useEffect } from "react";

export interface TourStep {
  id: string;
  target: string; // CSS selector for data-tour-id
  title: string;
  body: string;
  position: "center" | "right" | "bottom" | "left" | "top";
}

export const TOUR_STEPS: readonly TourStep[] = [
  {
    id: "canvas",
    target: '[data-tour-id="canvas"]',
    title: "Your workflow canvas",
    body: "Drag nodes here to build automation workflows. Double-click anywhere to quick-add a node.",
    position: "center",
  },
  {
    id: "palette",
    target: '[data-tour-id="palette"]',
    title: "Node palette",
    body: "Browse 70+ built-in nodes — HTTP, code, AI, data, integrations. Drag any node onto the canvas.",
    position: "right",
  },
  {
    id: "toolbar",
    target: '[data-tour-id="toolbar"]',
    title: "Quick-add shortcut",
    body: "Press Tab anywhere on the canvas to open the node search and insert a node at your cursor.",
    position: "bottom",
  },
  {
    id: "run",
    target: '[data-tour-id="run-button"]',
    title: "Run your workflow",
    body: "Click Run to execute all nodes. Live output appears on each node tile as it completes.",
    position: "bottom",
  },
  {
    id: "ai",
    target: '[data-tour-id="ai-draft-button"]',
    title: "AI workflow builder",
    body: "Type what you want to automate in plain English. AI generates the full workflow graph — editable.",
    position: "bottom",
  },
] as const;

const STORAGE_KEY = "nodyra-editor-tour-v1";

export function useOnboardingTour() {
  const [currentStep, setCurrentStep] = useState(0);
  const [isActive, setIsActive] = useState(() => {
    return localStorage.getItem(STORAGE_KEY) !== "true";
  });

  const totalSteps = TOUR_STEPS.length;
  const currentStepDef = TOUR_STEPS[currentStep] ?? null;
  const isFirstStep = currentStep === 0;
  const isLastStep = currentStep === totalSteps - 1;

  // Persist completion when currentStep advances past the last step
  useEffect(() => {
    if (currentStep >= totalSteps) {
      setIsActive(false);
      localStorage.setItem(STORAGE_KEY, "true");
    }
  }, [currentStep, totalSteps]);

  const next = useCallback(() => {
    setCurrentStep((prev) => Math.min(prev + 1, totalSteps));
  }, [totalSteps]);

  const prev = useCallback(() => {
    setCurrentStep((p) => Math.max(0, p - 1));
  }, []);

  const skip = useCallback(() => {
    setIsActive(false);
    localStorage.setItem(STORAGE_KEY, "true");
  }, []);

  const restart = useCallback(() => {
    setCurrentStep(0);
    setIsActive(true);
    localStorage.removeItem(STORAGE_KEY);
  }, []);

  return {
    isActive,
    currentStep,
    totalSteps,
    currentStepDef,
    next,
    prev,
    skip,
    restart,
    isFirstStep,
    isLastStep,
  } as const;
}
