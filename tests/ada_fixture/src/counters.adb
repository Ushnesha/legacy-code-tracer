package body Counters is
   procedure Bump is
   begin
      Total := Total + 1;
   end Bump;

   procedure Reset is
   begin
      Compute (Total);
   end Reset;

   procedure Compute (Result : out Integer) is
   begin
      Result := Total * 2;
   end Compute;

   procedure Accumulate (Acc : in out Integer; Step : in Integer) is
   begin
      Acc := Acc + Step;
   end Accumulate;

   protected body Guard is
      procedure Set (V : Integer) is
      begin
         Value := V;
      end Set;

      function Get return Integer is (Value);
   end Guard;

   task body Worker is
   begin
      Bump;
   end Worker;

   procedure Run (CB : Callback) is
   begin
      CB.all;
   end Run;
end Counters;
