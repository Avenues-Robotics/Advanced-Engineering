<callout icon="💡" color="blue_bg">
	**The big idea:** The Control Hub's built-in motor controller and a controller you write yourself can use the same math. The difference is **how often** each one checks the motor and changes its power. A custom controller can check much more often, so it reacts sooner.
</callout>
## Two ways to control a motor
When you want a motor to hold a speed or move to a position, something has to keep checking the motor and adjusting its power. That something is called a **feedback-based controller **(see <mention-page url="https://app.notion.com/p/3f7ec788f84081d0a468e7be52d79e62">Intro to Control Theory</mention-page> ). In FTC you have two choices for feedback-based controllers.
### Option 1: Let the hub do it (built-in controller)
You tell the hub what you want, and a small computer inside the hub takes care of the rest.
```java
motor.setVelocity(1500);                       // spin at 1500 ticks per second

motor.setTargetPosition(1000);                 // or go to position 1000
motor.setMode(DcMotor.RunMode.RUN_TO_POSITION);
motor.setPower(0.8);
```
### Option 2: Do it yourself (custom controller)
Your OpMode reads the motor, does the math, and sets the power every time through its loop.  See <mention-page url="https://app.notion.com/p/3f7ec788f84081d0a468e7be52d79e62">Intro to Control Theory</mention-page>  for full implementation details.
```java
while (opModeIsActive()) {
    double error = target - motor.getCurrentPosition();
    motor.setPower(kP * error);               // plus I, D and F terms
}
```
## An everyday comparison
Imagine two lifeguards watching the same pool.
- The **hub's controller** looks up from a book about every **45 ms**. If something happens just after it looks down, it won't notice until the next glance.
- The **custom controller** looks up every time your loop runs, usually every 5-10 ms. It notices almost right away, every time.
Both lifeguards know exactly what to do. One just checks a lot more often.
## Comparing Controllers
We sent velocities and positions to the built-in controller and a custom controller to 20 times each and timed how long it took for the motor to start moving.  The full testing procedure and all of the data is explain in <mention-page url="https://app.notion.com/p/41aec788f840807a8c2fc58e43ad647a">Rev Control Hub vs. Custom OpMode PIDF Comparison</mention-page>.
<table header-row="true">
<tr>
<td></td>
<td>Built-in (hub)</td>
<td>Custom (OpMode)</td>
</tr>
<tr>
<td>Average response time</td>
<td>about 30 ms</td>
<td>about 10 ms</td>
</tr>
<tr>
<td>Range of response times</td>
<td>about 8 to 55 ms</td>
<td>about 7 to 15 ms</td>
</tr>
<tr>
<td>Consistency</td>
<td>Varies a lot</td>
<td>Nearly the same every time</td>
</tr>
</table>
The charts show the difference. Each bar counts how many of the 20 tries landed in that time range.
<columns>
	<column>
		![Built-in controller: response times are spread out, because it depends on when the command arrives in the hub's cycle.](media/846cda716678d206-hub_velocity_response_times.svg)
	</column>
	<column>
		![Custom controller: response times are bunched together, because it acts on the very next loop.](media/439cc9b6aad848fd-custom_velocity_response_times.svg)
	</column>
</columns>
<callout icon="🔍" color="gray_bg">
	**Why the hub's times are so spread out:** If your command arrives just before the hub's next check, it reacts almost immediately. If it arrives just after, it has to wait for almost a whole cycle. That random wait is what makes the hub's results range from about 8 ms to 55 ms.
</callout>
## Two more things we found
### Speed readings are a bit old
`getVelocity()` doesn't measure speed at an instant. The hub counts encoder ticks over the last 50 ms and updates that number every 10 ms. That means the speed you read is about **33 ms behind** what the motor is actually doing. Both controllers use this same reading, so it slows down both of them when they control speed.
### Position readings are fresh
`getCurrentPosition()` gives you the encoder count at the moment you read it. In our test it was up to date on every single loop. This is why a custom **position** controller can react especially quickly.
## So which should you use?
<table header-row="true">
<tr>
<td>Use the built-in controller when...</td>
<td>Use a custom controller when...</td>
</tr>
<tr>
<td>A small delay doesn't matter (driving around, simple moves)</td>
<td>Quick reactions matter (arms, lifts, slides, flywheels that must recover after a shot)</td>
</tr>
<tr>
<td>Your OpMode loop is slow or busy (vision, lots of sensors)</td>
<td>Your loop is fast and steady (under about 10 ms)</td>
</tr>
<tr>
<td>You want something simple that just works</td>
<td>You want extra control, like holding an arm up against gravity</td>
</tr>
<tr>
<td>You're just getting started</td>
<td>Your team is comfortable tuning P, I, D and F</td>
</tr>
</table>
## Tips if you write your own
- **Keep your loop fast.** A custom controller is only as fast as your loop. If your loop takes 40 ms, you lose the advantage.
- **Use bulk reads.** They let you read every motor on a hub in one request instead of one request per motor.  See [Bulk Reads](https://gm0.org/en/latest/docs/software/tutorials/bulk-reads.html) to learn how.
- **Don't slow the loop down with extras.** Lots of telemetry, slow sensors (especially I2C sensors like color or distance sensors), and vision processing all make each loop longer.
- **Use position when you can.** Position readings are fresh; velocity readings are about 33 ms old.
- **Need faster speed readings?** Work out speed yourself from how much the position changed since the last loop. It's more up to date, but jumpier, so you'll want to smooth it out.
## A common misunderstanding
"The hub's controller is built in, so it must be faster." It's true that the hub doesn't have to send messages back and forth to your code. But in our test, it only updated the motor power about 20–25 times per second. A custom controller in a fast loop updated about 300 times per second.
## Want more details?
This reading is a short version of the full test report, which includes the method, all the data and the code: <mention-page url="https://app.notion.com/p/41aec788f840807a8c2fc58e43ad647a">Rev Control Hub vs. Custom OpMode PIDF Comparison</mention-page>