package Counters is
   Total : Integer := 0;

   procedure Bump;
   procedure Reset;
   procedure Compute (Result : out Integer);
   procedure Accumulate (Acc : in out Integer; Step : in Integer);

   protected type Guard is
      procedure Set (V : Integer);
      function Get return Integer;
   private
      Value : Integer := 0;
   end Guard;

   task type Worker;

   type Callback is access procedure;
   procedure Run (CB : Callback);
end Counters;
