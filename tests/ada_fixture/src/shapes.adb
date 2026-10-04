with Counters;

package body Shapes is
   procedure Draw (S : Circle) is
   begin
      Counters.Bump;
   end Draw;

   function Area (S : Circle) return Integer is (S.Radius * S.Radius * 3);

   procedure Draw (S : Square) is
   begin
      Counters.Bump;
   end Draw;

   function Area (S : Square) return Integer is (S.Side * S.Side);

   procedure Draw_All (S : Shape'Class) is
   begin
      Draw (S);
   end Draw_All;
end Shapes;
