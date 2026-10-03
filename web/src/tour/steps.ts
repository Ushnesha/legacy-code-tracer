// The guided tour of JSBSim. Each step sets the same URL state you could set by hand and shows one sentence.
//
// The sentences were checked against the JSBSim source (repos/jsbsim) and not just against the graph:
//  - FGFDMExec::Run (src/FGFDMExec.cpp) is called once per simulation step. It advances the clock (IncrTime),
//    runs the script, then loops `Models[i]->Run(holding)` over every model. It is not itself a loop.
//  - That `Models[i]->Run` is a virtual call on FGModel pointers: which override runs is only known at run
//    time. The extractor lists every override as a "may call" edge, so the dashed lines are possible targets.
//  - FGPropagate::Run (src/models/FGPropagate.cpp) integrates rotational and translational rate and position,
//    then updates the location, the transformation matrices and the orientation.
//  - FGPropagate::VehicleState::vLocation is the aircraft's position (an FGLocation); FGPropagate::Run and
//    several of its helpers assign to it.
//  - FGPropagate::GetAltitudeASL is a 4-line helper: radius minus sea-level radius.

export const TOUR_REPO = "jsbsim";

export type TourStep = { title: string; text: string };

export const TOUR_STEPS: TourStep[] = [
  { title: "The main call", text: "This runs once for every step of the flight. It moves the clock on, then gives each part of the aircraft model its turn." },
  { title: "Virtual calls", text: "Each dashed line is a virtual call. Which version runs is decided while the program runs, so the code alone doesn't say. We list every version it could reach." },
  { title: "Where the aircraft moves", text: "This is where the aircraft's position, speed and orientation are updated." },
  { title: "Who changes the position", text: "Everything in red changes this value, the aircraft's position. When many functions change one value, that is data coupling." },
  { title: "What a change could break", text: "Change this one function and here is everything that could be affected." },
];

export const QUALIFIED = {
  run: "JSBSim::FGFDMExec::Run",
  propagate: "JSBSim::FGPropagate::Run",
  position: "JSBSim::FGPropagate::VehicleState::vLocation",
  helper: "JSBSim::FGPropagate::GetAltitudeASL",
};
