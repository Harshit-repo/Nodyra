import { useState, useEffect, useCallback, useRef } from "react";
import { useOnboardingTour } from "./useOnboardingTour";
import type { TourStep } from "./useOnboardingTour";
import "./OnboardingTour.css";

interface TargetRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const TOOLTIP_OFFSET = 16;
const SPOTLIGHT_PADDING = 8;

function getTooltipStyle(
  rect: TargetRect,
  position: TourStep["position"],
): React.CSSProperties {
  const base: React.CSSProperties = { position: "fixed", zIndex: 10001 };
  const gap = TOOLTIP_OFFSET;

  switch (position) {
    case "center":
      return {
        ...base,
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
      };
    case "bottom":
      return {
        ...base,
        top: rect.top + rect.height + gap,
        left: rect.left + rect.width / 2,
        transform: "translateX(-50%)",
      };
    case "top":
      return {
        ...base,
        bottom: window.innerHeight - rect.top + gap,
        left: rect.left + rect.width / 2,
        transform: "translateX(-50%)",
      };
    case "right":
      return {
        ...base,
        top: rect.top + rect.height / 2,
        left: rect.left + rect.width + gap,
        transform: "translateY(-50%)",
      };
    case "left":
      return {
        ...base,
        top: rect.top + rect.height / 2,
        left: rect.left - gap,
        transform: "translate(-100%, -50%)",
      };
  }
}

function getSpotlightStyle(rect: TargetRect): React.CSSProperties {
  const p = SPOTLIGHT_PADDING;
  if (rect.width === 0 && rect.height === 0) {
    return {
      position: "fixed",
      top: "calc(50% - 120px)",
      left: "calc(50% - 200px)",
      width: "400px",
      height: "240px",
      borderRadius: "12px",
      boxShadow: "0 0 0 9999px rgba(0, 0, 0, 0.5)",
      zIndex: 10000,
      pointerEvents: "none" as const,
    };
  }
  return {
    position: "fixed",
    top: rect.top - p,
    left: rect.left - p,
    width: rect.width + p * 2,
    height: rect.height + p * 2,
    borderRadius: "8px",
    boxShadow: "0 0 0 9999px rgba(0, 0, 0, 0.5)",
    zIndex: 10000,
    pointerEvents: "none" as const,
  };
}

export function OnboardingTour() {
  const {
    isActive, currentStep, totalSteps, currentStepDef,
    next, prev, skip, isFirstStep, isLastStep,
  } = useOnboardingTour();
  const [targetRect, setTargetRect] = useState<TargetRect | null>(null);
  const [targetNotFound, setTargetNotFound] = useState(false);
  const pollRef = useRef<number>(0);
  const pollCountRef = useRef(0);

  const findTarget = useCallback(() => {
    if (!currentStepDef) return;
    const el = document.querySelector(currentStepDef.target) as HTMLElement | null;
    if (el) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 || r.height > 0 || currentStepDef.position === "center") {
        setTargetRect({ top: r.top, left: r.left, width: r.width, height: r.height });
        setTargetNotFound(false);
        return;
      }
      el.scrollIntoView?.({ behavior: "instant" as ScrollBehavior, block: "center" });
    }
    pollCountRef.current += 1;
    if (pollCountRef.current < 120) {
      pollRef.current = requestAnimationFrame(findTarget);
    } else {
      setTargetNotFound(true);
    }
  }, [currentStepDef]);

  useEffect(() => {
    if (!isActive || !currentStepDef) return;
    pollCountRef.current = 0;
    setTargetRect(null);
    setTargetNotFound(false);
    const timer = setTimeout(findTarget, 100);
    return () => {
      clearTimeout(timer);
      cancelAnimationFrame(pollRef.current);
    };
  }, [isActive, currentStep, currentStepDef, findTarget]);

  useEffect(() => {
    if (!isActive) return;
    const onResize = () => {
      pollCountRef.current = 0;
      findTarget();
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [isActive, findTarget]);

  if (!isActive || !currentStepDef) return null;

  return (
    <>
      {targetRect && !targetNotFound && (
        <div style={getSpotlightStyle(targetRect)} />
      )}
      {!targetNotFound && (
        <div
          style={
            targetRect
              ? getTooltipStyle(targetRect, currentStepDef.position)
              : { position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)", zIndex: 10001 }
          }
          className="onboarding-tooltip"
        >
          <div className="onboarding-tooltip-step">
            Step {currentStep + 1} of {totalSteps}
          </div>
          <h3 className="onboarding-tooltip-title">{currentStepDef.title}</h3>
          <p className="onboarding-tooltip-body">{currentStepDef.body}</p>
          <div className="onboarding-tooltip-actions">
            <button type="button" className="btn" onClick={skip}>
              Skip tour
            </button>
            <div className="onboarding-tooltip-nav">
              {!isFirstStep && (
                <button type="button" className="btn" onClick={prev}>
                  Previous
                </button>
              )}
              <button type="button" className="btn is-primary" onClick={next}>
                {isLastStep ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        </div>
      )}
      {targetNotFound && (
        <div
          style={{
            position: "fixed",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            zIndex: 10001,
          }}
          className="onboarding-tooltip"
        >
          <div className="onboarding-tooltip-step">
            Step {currentStep + 1} of {totalSteps}
          </div>
          <h3 className="onboarding-tooltip-title">{currentStepDef.title}</h3>
          <p className="onboarding-tooltip-body">{currentStepDef.body}</p>
          <p className="onboarding-tooltip-note">
            (The highlighted element is not currently visible. It will appear when
            you interact with the editor.)
          </p>
          <div className="onboarding-tooltip-actions">
            <button type="button" className="btn" onClick={skip}>
              Skip tour
            </button>
            <div className="onboarding-tooltip-nav">
              {!isFirstStep && (
                <button type="button" className="btn" onClick={prev}>
                  Previous
                </button>
              )}
              <button type="button" className="btn is-primary" onClick={next}>
                {isLastStep ? "Finish" : "Next"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
