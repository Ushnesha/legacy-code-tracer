with Counters;
with Shapes;

procedure Main is
   X : Integer;
   Y : Integer := 5;
   C : Shapes.Circle;
   Q : Shapes.Square;
   G : Counters.Guard;
begin
   Counters.Compute (X);
   Counters.Accumulate (Y, X);
   Shapes.Draw_All (C);
   Shapes.Draw_All (Q);
   Shapes.Draw (C);
   G.Set (Y);
end Main;
